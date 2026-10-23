"""Readers that turn files and URLs into xarray Datasets (rasters) or GeoDataFrames (vectors).

`read` is the single entry point. It is used both for a first load and to reopen a data_ref
from its stored options, so both paths behave identically.
"""

from loci.errors import LoadError
from loci.io.common import Data, ReadResult
from loci.io.formats import TABLE_FORMATS, Format, detect_format, is_remote
from loci.io.raster import open_raster
from loci.io.stac import load_stac, read_stac_items
from loci.io.table import read_points_table
from loci.io.vector import read_vector
from loci.models import ReadOptions

# Formats created by loci itself rather than detected from a suffix.
STAC_ITEMS = Format("vector", "stac-items")
STAC_RASTER = Format("raster", "stac-raster")
STAC_OPTIONS = ("bands", "resolution", "bbox", "output_crs", "resampling", "drop_offset")
FILE_OPTIONS = ("layer", "x_col", "y_col", "crs")

__all__ = [
    "STAC_ITEMS",
    "STAC_RASTER",
    "Data",
    "Format",
    "ReadResult",
    "detect_format",
    "is_remote",
    "read",
]


def read(uri: str, fmt: Format, options: ReadOptions) -> ReadResult:
    _check_options(fmt, options)
    if fmt == STAC_ITEMS:
        return read_stac_items(uri, fmt.name, options)
    if fmt == STAC_RASTER:
        return load_stac(uri, fmt.name, options)
    if fmt.kind == "raster":
        return open_raster(uri, fmt.name, options)
    if fmt.name == "csv":
        return read_points_table(uri, fmt.name, options)
    return read_vector(uri, fmt.name, options)


def _check_options(fmt: Format, options: ReadOptions) -> None:
    """Reject options that do not apply to the format, rather than silently ignoring them."""
    given = {name for name, value in options if value is not None}
    if fmt == STAC_ITEMS:
        if given:
            raise LoadError(
                "Search results take no read options.",
                hint="To load their pixels, call load with the search's data_ref and bands=.",
            )
        return
    if fmt == STAC_RASTER:
        misplaced = sorted(given & set(FILE_OPTIONS))
        if misplaced:
            raise LoadError(
                f"{', '.join(o + '=' for o in misplaced)} do not apply to STAC items.",
                hint="Use output_crs= to choose the output grid's CRS (items in other CRSs are "
                "then reprojected).",
            )
        return
    misplaced = sorted(given & set(STAC_OPTIONS))
    if misplaced:
        raise LoadError(
            f"{', '.join(o + '=' for o in misplaced)} apply only to STAC items from stac_search."
        )
    if options.layer is not None and (fmt.kind == "raster" or fmt.name in TABLE_FORMATS):
        raise LoadError(
            "layer= applies only to vector files with layers (GeoPackage, GeoJSON, Shapefile "
            "and other OGR formats)."
        )
    if fmt.name not in TABLE_FORMATS and (options.x_col or options.y_col):
        raise LoadError(
            "x_col= and y_col= apply only to coordinate tables (CSV, or Parquet without "
            "GeoParquet metadata)."
        )
