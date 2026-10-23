"""Open rasters lazily as xarray Datasets with one data variable per band."""

import atexit
import os
from pathlib import PurePath
from typing import Any

import rasterio
import rioxarray
import xarray as xr
from xarray.backends.file_manager import FILE_CACHE

from loci.errors import LoadError
from loci.io.common import ReadResult, resolve_crs
from loci.models import ReadOptions

# Opening one file over HTTP should not list its directory first; this matters a lot for COGs.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

# Close cached file handles before interpreter teardown. Otherwise their finalisers run after
# the modules they need are gone and print "Error in sys.excepthook" to stderr at exit.
atexit.register(FILE_CACHE.clear)


def open_raster(
    uri: str, fmt: str, options: ReadOptions, *, overview_level: int | None = None
) -> ReadResult:
    """Open a raster without reading pixels. Bands are named from their descriptions if possible.

    Pixel values are kept raw (no masking or scaling), so dtype and nodata are reported as stored.
    `overview_level` opens a reduced-resolution copy stored in the file (0 is the first overview).
    """
    extra: dict[str, Any] = {} if overview_level is None else {"overview_level": overview_level}
    try:
        opened = rioxarray.open_rasterio(
            uri, chunks=True, mask_and_scale=False, band_as_variable=True, **extra
        )
        descriptions = _descriptions(uri)
    except (rasterio.errors.RasterioError, OSError) as e:
        raise LoadError(
            f"Cannot open {uri!r} as a raster: {e}",
            hint="Check the path or URL. Remote files need http(s)://, s3:// or a /vsi prefix.",
        ) from e
    if not isinstance(opened, xr.Dataset):
        raise LoadError(
            f"{uri!r} contains several subdatasets; loci reads one raster at a time.",
            hint="Open a single subdataset by its GDAL name.",
        )

    ds = _name_bands(opened, descriptions)
    crs, crs_source, warnings = resolve_crs(
        ds.rio.crs, options.crs, what=f"Raster {PurePath(uri).name!r}"
    )
    if crs_source == "argument":
        ds = ds.rio.write_crs(crs)
    return ReadResult(ds, "raster", fmt, options, crs_source, warnings)


def overview_factors(uri: str) -> list[int]:
    """Decimation factors of the overviews stored in a raster file, e.g. [2, 4, 8]."""
    with rasterio.open(uri) as src:
        return list(src.overviews(1))


def _descriptions(uri: str) -> list[str | None]:
    # Read from rasterio rather than rioxarray's long_name attribute, which rioxarray leaves out
    # when an overview level is opened (and when overview_level=None is passed explicitly).
    with rasterio.open(uri) as src:
        return list(src.descriptions)


def _name_bands(ds: xr.Dataset, descriptions: list[str | None]) -> xr.Dataset:
    """Rename band_1..n to the band descriptions, if every band has a distinct one."""
    names = [d for d in descriptions if d]
    if len(names) == len(ds.data_vars) and len(set(names)) == len(names):
        return ds.rename(dict(zip(ds.data_vars, names, strict=True)))
    return ds
