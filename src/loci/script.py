"""Helpers for scripts executed by the `run` tool.

Inside a run:

    from loci import script

    scene = script.read("scene")                 # a declared input, by name
    factor = script.parameters()["factor"]
    script.save(result, "ndvi.tif")              # written to the run's outputs/ folder

Only inputs declared in run(inputs={...}) can be read this way, so the provenance of every output
lists everything the script read through loci. Anything saved under outputs/ is registered when
the run succeeds.

read() gives physical values for raster bands that hold measurements: scale and offset applied,
nodata as NaN (see physical()). Stored values are a trap: raw bands are often unsigned integers,
so nir - red wraps around wherever red is brighter, and xarray carries attributes through
arithmetic, so a ratio of raw bands would inherit their scale_factor. Integer bands without
scaling (class maps, bit flags) stay as stored, and read(name, raw=True) returns every band as
stored. save() never writes inherited scaling for float bands and records a warning when it drops
one.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci.errors import LociError
from loci.io import Data
from loci.io.write import write_geotiff
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
    data: dict[str, str] = json.loads((run_dir() / INPUTS_FILE).read_text(encoding="utf-8"))
    return data


def parameters() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((run_dir() / PARAMETERS_FILE).read_text(encoding="utf-8"))
    return data


def read(name: str, *, raw: bool = False) -> Data:
    """The data behind a declared input.

    Raster bands that hold measurements come as physical values (see physical()): bands that
    declare a scale or offset, and float bands with a nodata value. Other integer bands, such as
    class maps and bit flags, stay as stored. raw=True returns every band as stored.
    """
    declared = inputs()
    if name not in declared:
        raise LociError(
            f"No input named {name!r}; declared inputs: {sorted(declared)}.",
            hint="Declare every input in run(inputs={name: data_ref}) so provenance lists it.",
        )
    data = Workspace.from_env().open(declared[name])
    if raw or not isinstance(data, xr.Dataset):
        return data
    return data.assign({n: physical(data[n]) for n in data.data_vars if _measurement(data[n])})


def _measurement(da: xr.DataArray) -> bool:
    scaling = (da.attrs.get("scale_factor", 1.0), da.attrs.get("add_offset", 0.0))
    nodata = da.rio.nodata
    masked_float = da.dtype.kind == "f" and nodata is not None and not np.isnan(nodata)
    return scaling != (1.0, 0.0) or masked_float


def physical(da: xr.DataArray) -> xr.DataArray:
    """Physical values as floats: nodata to NaN, scale and offset applied, packing attrs dropped.

    read() already does this for measurement bands; use it on bands read with raw=True.
    """
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
    with (run_dir() / WARNINGS_FILE).open("a", encoding="utf-8") as f:
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
        for note in write_geotiff(obj, path):
            warn(f"{path.name}: {note}")
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
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    else:
        raise TypeError(
            f"Cannot save {type(obj).__name__} as {suffix or 'a file without a suffix'}. "
            "Rasters go to .tif; GeoDataFrames to .gpkg, .geojson, .shp or .parquet; "
            "DataFrames to .csv or .parquet; dicts and lists to .json."
        )
    return path
