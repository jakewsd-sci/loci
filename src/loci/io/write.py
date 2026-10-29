"""Writers that turn rasters (xarray Datasets) and vectors (GeoDataFrames) into files.

Data are written as they are held: nothing is reprojected or resampled, and nodata, scale and
offset are kept. Where a format cannot hold the data as they are, the writer refuses and says
what to do instead; where it changes something, such as giving every band one data type, it
returns a note saying so. `run` outputs (loci.script.save) and exports from the app share them.
"""

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci.errors import LociError

RASTER_FORMATS = {"geotiff": ".tif", "netcdf": ".nc"}
VECTOR_FORMATS = {"gpkg": ".gpkg", "geojson": ".geojson", "csv": ".csv"}
MAX_BYTES = 4 * 1024**3  # larger exports belong in a script that saves just what it needs


def write_raster(
    ds: xr.Dataset, path: Path, *, step: int | None = None, fmt: str = "geotiff"
) -> list[str]:
    """Write a raster as a GeoTIFF, one band per variable and a time stack's chosen step, or as
    NetCDF, which holds every time step."""
    if fmt == "netcdf":
        return write_netcdf(ds, path)
    notes = []
    if "time" in ds.dims:
        count = ds.sizes["time"]
        if step is None or not 0 <= step < count:
            first = str(ds["time"].values[0])[:10]
            raise LociError(
                f"This raster holds {count} time steps, and a GeoTIFF holds one.",
                hint=f"Choose the step to export: 0 for {first}, up to {count - 1}.",
            )
        ds = ds.isel(time=[step])
        notes.append(f"Wrote the time step of {str(ds['time'].values[0])[:10]} (step {step}).")
    if not ds.data_vars:
        raise LociError("This raster has no bands to write.")
    if ds.nbytes > MAX_BYTES:
        raise LociError(
            f"This raster is {ds.nbytes / 1024**3:.1f} GB, too large to export from the app.",
            hint="Write a script that saves only what you need with loci.script.save.",
        )
    dtypes = sorted({str(band.dtype) for band in ds.data_vars.values()})
    if len(dtypes) > 1:
        common = np.result_type(*[band.dtype for band in ds.data_vars.values()])
        notes.append(
            f"A GeoTIFF gives every band one data type: wrote {common} (from {', '.join(dtypes)})."
        )
    return notes + write_geotiff(ds, path)


def write_netcdf(ds: xr.Dataset, path: Path) -> list[str]:
    """Write a raster as CF NetCDF with every time step, its CRS as a grid_mapping, values as
    stored, and scale, offset and fill value as attributes."""
    if ds.nbytes > MAX_BYTES:
        raise LociError(
            f"This raster is {ds.nbytes / 1024**3:.1f} GB, too large to export from the app.",
            hint="Write a script that saves only what you need with loci.script.save.",
        )
    out = ds.copy()
    for name in out.data_vars:
        out[name].encoding = {}  # attributes say how values are stored; nothing is re-encoded
    if ds.rio.crs is not None:
        out = out.rio.write_crs(ds.rio.crs)
    out.to_netcdf(path, engine="netcdf4")
    return []


def write_vector(
    gdf: gpd.GeoDataFrame, path: Path, fmt: str, *, name: str | None = None
) -> list[str]:
    """Write a vector layer as GeoPackage, GeoJSON or CSV; notes say what the format leaves out.
    `name` names the layer in a GeoPackage (default: the file's name)."""
    epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    named = f"EPSG:{epsg}" if epsg else (gdf.crs.name if gdf.crs is not None else "an unknown CRS")
    if fmt == "gpkg":
        layer = re.sub(r"[^A-Za-z0-9_]+", "_", name or path.stem).strip("_") or "layer"
        gdf.to_file(path, driver="GPKG", layer=layer)
        return []
    if fmt == "geojson":
        gdf.to_file(path, driver="GeoJSON")
        if epsg == 4326:
            return []
        return [
            f"GeoJSON is meant to be in WGS 84; this file keeps {named}, which some tools "
            "misread. Reproject it in a script if they need WGS 84."
        ]
    if fmt == "csv":
        return _write_csv(gdf, path, named)
    raise LociError(
        f"loci cannot write vectors as {fmt!r}.", hint=f"Use {', '.join(VECTOR_FORMATS)}."
    )


def _write_csv(gdf: gpd.GeoDataFrame, path: Path, named: str) -> list[str]:
    table = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    geometry = gdf.geometry
    present = geometry.dropna()
    if len(present) and (present.geom_type == "Point").all():
        x, y = ("lon", "lat") if gdf.crs is not None and gdf.crs.is_geographic else ("x", "y")
        if not (_holds(table, x, geometry.x) and _holds(table, y, geometry.y)):
            # The point table's own coordinate columns are kept as they are; else add them.
            if x in table.columns or y in table.columns:
                x, y = f"geometry_{x}", f"geometry_{y}"
            table.insert(0, y, geometry.y)
            table.insert(0, x, geometry.x)
        note = f"CSV holds no CRS: the columns {x} and {y} are in {named}."
    else:
        table.insert(0, "wkt", geometry.to_wkt())
        note = f"CSV holds no CRS: the wkt column is in {named}."
    table.to_csv(path, index=False, encoding="utf-8")
    return [note]


def _holds(table: pd.DataFrame, column: str, values: pd.Series) -> bool:
    """Whether a table's column already holds these coordinates (a point table's own)."""
    if column not in table.columns:
        return False
    given = pd.to_numeric(table[column], errors="coerce").to_numpy(dtype=float)
    return bool(np.allclose(given, values.to_numpy(dtype=float), equal_nan=True))


def write_geotiff(data: xr.Dataset | xr.DataArray, path: Path) -> list[str]:
    """One band per data variable, named by band descriptions, with CRS, nodata and scaling.

    Returns notes on anything not written as it was held, such as a NaN nodata on integers."""
    notes: list[str] = []
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
    nodata = _nodata(bands[0], notes)
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
        dst.scales, dst.offsets = _packing(names, bands, dtype, notes)
    return notes


def _nodata(band: xr.DataArray, notes: list[str]) -> float | None:
    """The band's nodata. Integer bands cannot hold a NaN nodata, which they inherit through
    arithmetic on float bands (e.g. a count of valid pixels); it is left out, with a note."""
    try:
        nodata: float | None = band.rio.nodata
    except ValueError:
        notes.append(
            "integer band(s) carry a NaN nodata, probably inherited from float inputs through "
            "arithmetic. It was not written, since integers cannot be NaN."
        )
        return None
    return nodata


def _packing(
    names: list[str], bands: list[xr.DataArray], dtype: np.dtype, notes: list[str]
) -> tuple[list[float], list[float]]:
    """Scale and offset to write: kept for integer bands (packing), dropped for float bands."""
    scales = [float(b.attrs.get("scale_factor", 1.0)) for b in bands]
    offsets = [float(b.attrs.get("add_offset", 0.0)) for b in bands]
    if dtype.kind in "iu":
        return scales, offsets
    inherited = [n for n, s, o in zip(names, scales, offsets, strict=True) if (s, o) != (1.0, 0.0)]
    if inherited:
        notes.append(
            f"float band(s) {inherited} carry scale_factor/add_offset attributes, probably "
            "inherited from raw inputs through arithmetic. They were not written: float outputs "
            "are stored as physical values. Use script.physical() on raw bands first."
        )
    return [1.0] * len(bands), [0.0] * len(bands)
