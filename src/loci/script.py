"""Helpers for scripts executed by the `run` tool.

Inside a run:

    from loci import script

    scene = script.read("scene")                 # a declared input, by name
    factor = script.parameters()["factor"]
    script.save(result, "ndvi.tif")              # written to the run's outputs/ folder

Only inputs declared in run(inputs={...}) can be read this way, so the provenance of every output
lists everything the script read through loci. Anything saved under outputs/ is registered when
the run succeeds.

Compute on physical values: script.physical(stack["red"]) masks nodata, applies scale and offset,
and drops those attributes. xarray carries attributes through arithmetic, so a ratio of raw bands
would otherwise inherit their scale_factor; save() never writes inherited scaling for float bands
and records a warning when it drops one.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci.errors import LociError
from loci.io import Data
from loci.workspace import Workspace

RUN_DIR_ENV = "LOCI_RUN_DIR"
INPUTS_FILE = "inputs.json"
PARAMETERS_FILE = "parameters.json"
OUTPUTS_DIR = "outputs"
WARNINGS_FILE = "warnings.txt"
PACKING_ATTRS = ("scale_factor", "add_offset", "_FillValue", "nodata", "missing_value")
VECTOR_SUFFIXES = {".gpkg", ".geojson", ".shp", ".parquet"}


def run_dir() -> Path:
    value = os.environ.get(RUN_DIR_ENV)
    if not value:
        raise LociError(
            "loci.script works only inside a script started by the run tool.",
            hint="Call run(code=...) or run(path=...) to execute the script.",
        )
    return Path(value)


def inputs() -> dict[str, str]:
    """Declared inputs: name -> data_ref."""
    data: dict[str, str] = json.loads((run_dir() / INPUTS_FILE).read_text())
    return data


def parameters() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((run_dir() / PARAMETERS_FILE).read_text())
    return data


def read(name: str) -> Data:
    """The data behind a declared input."""
    declared = inputs()
    if name not in declared:
        raise LociError(
            f"No input named {name!r}; declared inputs: {sorted(declared)}.",
            hint="Declare every input in run(inputs={name: data_ref}) so provenance lists it.",
        )
    return Workspace.from_env().open(declared[name])


def physical(da: xr.DataArray) -> xr.DataArray:
    """Physical values as floats: nodata to NaN, scale and offset applied, packing attrs dropped."""
    nodata = da.rio.nodata
    values = da.where(da != nodata) if nodata is not None else da
    dtype = np.float64 if da.dtype == np.float64 else np.float32
    scaled = values.astype(dtype) * da.attrs.get("scale_factor", 1.0) + da.attrs.get(
        "add_offset", 0.0
    )
    scaled.attrs = {k: v for k, v in da.attrs.items() if k not in PACKING_ATTRS}
    scaled.encoding = {}
    return scaled.rio.write_nodata(np.nan)


def warn(message: str) -> None:
    """Record a warning that run reports in its result (and prints it to stderr)."""
    with (run_dir() / WARNINGS_FILE).open("a") as f:
        f.write(message.replace("\n", " ") + "\n")
    print(f"loci.script warning: {message}", file=sys.stderr)


def output_path(filename: str) -> Path:
    """Where to write an output file by other means; it is registered after the run."""
    if not filename or Path(filename).name != filename or filename.startswith("."):
        raise ValueError(f"Output names are plain file names, such as 'ndvi.tif': {filename!r}")
    outputs = run_dir() / OUTPUTS_DIR
    outputs.mkdir(exist_ok=True)
    return outputs / filename


def save(obj: Any, filename: str) -> Path:
    """Write an output by suffix: rasters to .tif, vectors to .gpkg/.geojson/.shp/.parquet,
    tables to .csv/.parquet and plain data to .json."""
    path = output_path(filename)
    suffix = path.suffix.lower()
    if isinstance(obj, xr.Dataset | xr.DataArray) and suffix in {".tif", ".tiff"}:
        write_geotiff(obj, path)
    elif isinstance(obj, gpd.GeoDataFrame) and suffix in VECTOR_SUFFIXES:
        if suffix == ".parquet":
            obj.to_parquet(path)
        else:
            obj.to_file(path)
    elif isinstance(obj, pd.DataFrame) and suffix in {".csv", ".parquet"}:
        if suffix == ".csv":
            obj.to_csv(path, index=False)
        else:
            obj.to_parquet(path)
    elif isinstance(obj, dict | list) and suffix == ".json":
        path.write_text(json.dumps(obj, indent=2, default=str))
    else:
        raise TypeError(
            f"Cannot save {type(obj).__name__} as {suffix or 'a file without a suffix'}. "
            "Rasters go to .tif; GeoDataFrames to .gpkg, .geojson, .shp or .parquet; "
            "DataFrames to .csv or .parquet; dicts and lists to .json."
        )
    return path


def write_geotiff(data: xr.Dataset | xr.DataArray, path: Path) -> None:
    """One band per data variable, named by band descriptions, with CRS, nodata and scaling."""
    ds = (
        data.to_dataset(name=str(data.name or "band_1")) if isinstance(data, xr.DataArray) else data
    )
    if "time" in ds.dims:
        if ds.sizes["time"] != 1:
            raise ValueError(
                f"A GeoTIFF holds one time step, but this has {ds.sizes['time']}. Reduce over "
                "time (e.g. .median('time')) or save each step to its own file."
            )
        ds = ds.isel(time=0, drop=True)
    names = [str(n) for n in ds.data_vars]
    bands = [ds[n].transpose("y", "x") for n in names]
    dtype = np.result_type(*[b.dtype for b in bands])
    dtype = np.dtype("uint8") if dtype.kind == "b" else dtype
    nodata = bands[0].rio.nodata
    if nodata is None and np.issubdtype(dtype, np.floating):
        nodata = float("nan")
    height, width = bands[0].shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=len(bands),
        dtype=dtype.name,
        crs=ds.rio.crs,
        transform=ds.rio.transform(),
        nodata=nodata,
        compress="deflate",
    ) as dst:
        for i, (name, band) in enumerate(zip(names, bands, strict=True), start=1):
            dst.write(np.asarray(band.values).astype(dtype), i)
            dst.set_band_description(i, name)
        dst.scales, dst.offsets = _packing(names, bands, dtype, path.name)


def _packing(
    names: list[str], bands: list[xr.DataArray], dtype: np.dtype, filename: str
) -> tuple[list[float], list[float]]:
    """Scale and offset to write: kept for integer bands (packing), dropped for float bands."""
    scales = [float(b.attrs.get("scale_factor", 1.0)) for b in bands]
    offsets = [float(b.attrs.get("add_offset", 0.0)) for b in bands]
    if dtype.kind in "iu":
        return scales, offsets
    inherited = [n for n, s, o in zip(names, scales, offsets, strict=True) if (s, o) != (1.0, 0.0)]
    if inherited:
        warn(
            f"{filename}: float band(s) {inherited} carry scale_factor/add_offset attributes, "
            "probably inherited from raw inputs through arithmetic. They were not written: float "
            "outputs are stored as physical values. Use script.physical() on raw bands first."
        )
    return [1.0] * len(bands), [0.0] * len(bands)
