"""Building a tool result from a workspace record and its data."""

import geopandas as gpd
import xarray as xr

from loci.io import Data
from loci.models import RasterSummary, RefSummary, VectorSummary
from loci.summary.raster import StatsSource, raster_notes, raster_warnings, summarise_raster
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
        notes=[*context, *(notes or [])],
    )
