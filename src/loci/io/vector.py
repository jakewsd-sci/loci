"""Read vector files (GeoPackage, GeoJSON, Shapefile, GeoParquet) as GeoDataFrames."""

from pathlib import PurePath

import geopandas as gpd
import pyarrow.parquet as pq
import pyogrio
import pyogrio.errors

from loci.errors import LoadError
from loci.io.common import ReadResult, resolve_crs
from loci.io.table import read_points_table
from loci.models import ReadOptions


def read_vector(uri: str, fmt: str, options: ReadOptions) -> ReadResult:
    """Read every feature. Parquet without GeoParquet metadata is read as a coordinate table."""
    if fmt == "parquet" and not _has_geo_metadata(uri):
        return read_points_table(uri, fmt, options)

    warnings: list[str] = []
    try:
        if fmt == "parquet":
            gdf = gpd.read_parquet(uri)
        else:
            layer, warnings = _choose_layer(uri, options.layer)
            options = options.model_copy(update={"layer": layer})
            gdf = gpd.read_file(uri, layer=layer, engine="pyogrio")
    except (pyogrio.errors.DataSourceError, pyogrio.errors.DataLayerError, OSError) as e:
        raise LoadError(f"Cannot read {uri!r} as vector data: {e}") from e

    crs, crs_source, crs_warnings = resolve_crs(
        gdf.crs, options.crs, what=f"Vector {PurePath(uri).name!r}"
    )
    if crs_source == "argument":
        gdf = gdf.set_crs(crs)
    return ReadResult(gdf, "vector", fmt, options, crs_source, warnings + crs_warnings)


def _choose_layer(uri: str, requested: str | None) -> tuple[str | None, list[str]]:
    """The layer to read. With several layers and none requested, the first, with a warning."""
    try:
        layers = [str(name) for name, _ in pyogrio.list_layers(uri)]
    except pyogrio.errors.DataSourceError as e:
        raise LoadError(f"Cannot open {uri!r} as vector data: {e}") from e
    if requested is not None:
        if requested not in layers:
            raise LoadError(f"No layer {requested!r}; available layers: {layers}.")
        return requested, []
    if len(layers) > 1:
        return layers[0], [
            f"Source has {len(layers)} layers {layers}; read {layers[0]!r}. "
            "Pass layer= to read another."
        ]
    return None, []


def _has_geo_metadata(uri: str) -> bool:
    try:
        metadata = pq.read_schema(uri).metadata or {}
    except OSError as e:
        raise LoadError(f"Cannot read {uri!r} as Parquet: {e}") from e
    return b"geo" in metadata
