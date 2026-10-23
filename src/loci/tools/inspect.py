"""The `inspect` tool: a compact description of any data_ref, with raster statistics."""

import xarray as xr

from loci.models import RefSummary
from loci.summary.raster import DEFAULT_MAX_PIXELS
from loci.tools.common import describe, stats_source
from loci.workspace import Workspace

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
        source = stats_source(record, data, max_pixels)
    return describe(record, data, stats=source, warnings=warnings)
