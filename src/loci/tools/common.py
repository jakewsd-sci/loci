"""What tools share: building a result from a record, and choosing a raster statistics sample."""

import geopandas as gpd
import rasterio
import xarray as xr

from loci.errors import LoadError
from loci.io import Data
from loci.io.raster import open_raster, overview_factors
from loci.models import RasterSummary, RefSummary, VectorSummary
from loci.summary.raster import (
    StatsSource,
    decimation_for,
    plan_stats,
    raster_notes,
    raster_warnings,
    summarise_raster,
)
from loci.summary.vector import summarise_vector, vector_notes, vector_warnings
from loci.workspace import RefRecord


def describe(
    record: RefRecord,
    data: Data,
    *,
    stats: StatsSource | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
) -> RefSummary:
    """Summary of a data_ref, with warnings from its load, the caller and the summary itself."""
    summary: RasterSummary | VectorSummary
    if isinstance(data, xr.Dataset):
        summary = summarise_raster(data, stats=stats, crs_source=record.crs_source)
        found, context = raster_warnings(summary), raster_notes(summary)
    elif isinstance(data, gpd.GeoDataFrame):
        summary = summarise_vector(data, crs_source=record.crs_source)
        found, context = vector_warnings(summary), vector_notes(summary)
    else:
        raise TypeError(f"Unsupported data type {type(data).__name__}")
    return RefSummary(
        data_ref=record.data_ref,
        kind=record.kind,
        summary=summary,
        warnings=[*record.load_warnings, *(warnings or []), *found],
        notes=[*record.load_notes, *context, *(notes or [])],
    )


def stats_source(record: RefRecord, ds: xr.Dataset, max_pixels: int) -> StatsSource:
    """Pixels to compute statistics from: all of them, a stored overview, or a strided sample.

    Never more than max_pixels per band. An overview is preferred to striding when the file has
    one, because reading it costs a fraction of the I/O.
    """
    needed = decimation_for(ds, max_pixels)
    if needed > 1 and "time" not in ds.dims:
        overview = _overview(record, needed)
        if overview is not None:
            return overview
    return plan_stats(ds, max_pixels)


def _overview(record: RefRecord, needed: int) -> StatsSource | None:
    """The finest stored overview at least `needed` times coarser, if the file has one."""
    uri, options = record.source.uri, record.source.options
    try:
        factors = overview_factors(uri)
    except rasterio.errors.RasterioError:
        return None
    for level, factor in enumerate(factors):
        if factor >= needed:
            try:
                ds = open_raster(uri, record.source.format, options, overview_level=level).data
            except LoadError:
                return None
            assert isinstance(ds, xr.Dataset)
            return StatsSource(ds, "overview", factor)
    return None
