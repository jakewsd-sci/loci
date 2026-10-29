"""Work out what kind of data a URI holds, and which reader to use, from its suffix."""

from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import urlparse

from loci.errors import UnsupportedFormatError
from loci.models import Kind

REMOTE_SCHEMES = frozenset({"http", "https", "s3", "gs", "az"})


@dataclass(frozen=True)
class Format:
    kind: Kind
    name: str


SUFFIXES: dict[str, Format] = {
    ".tif": Format("raster", "geotiff"),
    ".tiff": Format("raster", "geotiff"),
    ".gpkg": Format("vector", "gpkg"),
    ".geojson": Format("vector", "geojson"),
    ".json": Format("vector", "geojson"),
    ".shp": Format("vector", "shapefile"),
    ".parquet": Format("vector", "parquet"),
    ".geoparquet": Format("vector", "parquet"),
    ".csv": Format("vector", "csv"),
    ".nc": Format("raster", "netcdf"),
    ".nc4": Format("raster", "netcdf"),
    ".netcdf": Format("raster", "netcdf"),
    ".zarr": Format("raster", "zarr"),  # a folder, or a URL of one
    ".zarr-v2": Format("raster", "zarr"),
    ".zarr-v3": Format("raster", "zarr"),
}

# Formats read as tables of coordinates rather than as geometries. Parquet files without
# GeoParquet metadata are read as tables too; that is decided when the file is opened.
TABLE_FORMATS = frozenset({"csv", "parquet"})


def is_remote(uri: str) -> bool:
    return urlparse(uri).scheme in REMOTE_SCHEMES or uri.startswith("/vsi")


def uri_suffix(uri: str) -> str:
    """Lower-case suffix of a local path or URL, ignoring any query string."""
    path = urlparse(uri).path if is_remote(uri) else uri
    return PurePath(path).suffix.lower()


def detect_format(uri: str, kind: Kind | None = None) -> Format:
    """Format for a URI. `kind` is needed only for suffixes loci does not recognise.

    Unrecognised suffixes are handed to GDAL (rasters) or OGR (vectors) as-is.
    """
    suffix = uri_suffix(uri)
    fmt = SUFFIXES.get(suffix)
    if fmt is None:
        if kind is None:
            raise UnsupportedFormatError(
                f"Cannot tell the format of {uri!r} from its suffix ({suffix or 'none'}).",
                hint=f"Supported suffixes: {', '.join(SUFFIXES)}. To try another GDAL or OGR "
                "format, pass kind='raster' or kind='vector'.",
            )
        return Format(kind, "gdal" if kind == "raster" else "ogr")
    if kind is not None and kind != fmt.kind:
        raise UnsupportedFormatError(
            f"{suffix} files are read as {fmt.kind} data, not {kind}.",
            hint="Omit kind, or pass the kind that matches the file.",
        )
    return fmt
