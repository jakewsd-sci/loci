"""The `inspect` tool: a compact description of any data_ref, with raster statistics."""

import rasterio
import xarray as xr

from loci.errors import LoadError
from loci.io.raster import open_raster, overview_factors
from loci.models import RefSummary
from loci.summary.raster import DEFAULT_MAX_PIXELS, StatsSource, decimation_for, plan_stats
from loci.tools.common import describe
from loci.workspace import RefRecord, Workspace

SOURCE_CHANGED = (
    "The source file has changed since it was loaded (its size or modification time differs), "
    "so this description may not match the data first loaded. Load it again for a fresh data_ref."
)


def inspect(
    ws: Workspace, data_ref: str, *, stats: bool = True, max_pixels: int = DEFAULT_MAX_PIXELS
) -> RefSummary:
    """Describe a data_ref. For rasters, also compute per-band statistics.

    Raster statistics read at most `max_pixels` values per band, from an overview stored in the
    file when there is one, otherwise from a strided sample; the summary says which. Vector
    summaries are always complete.
    """
    record = ws.record(data_ref)
    data = ws.open(data_ref)
    warnings = [SOURCE_CHANGED] if ws.source_changed(record) else []
    source = None
    if stats and isinstance(data, xr.Dataset):
        source = _stats_source(record, data, max_pixels)
    return describe(record, data, stats=source, warnings=warnings)


def _stats_source(record: RefRecord, ds: xr.Dataset, max_pixels: int) -> StatsSource:
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
