"""Real data over the network. Deselected by default; run with `uv run pytest -m network`."""

import pytest

from loci.models import RasterSummary
from loci.tools.inspect import inspect
from loci.tools.load import load
from loci.workspace import Workspace

pytestmark = pytest.mark.network

# Sentinel-2 L2A scene classification (20 m) over eastern Sicily, from Earth Search's public
# sentinel-cogs bucket: item S2A_33SVA_20240630_0_L2A.
SCL_COG = (
    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
    "33/S/VA/2024/6/S2A_33SVA_20240630_0_L2A/SCL.tif"
)
SCL_CLASSES = range(12)  # scene classification codes 0..11


def test_remote_cog_loads_lazily_and_inspects_from_an_overview(ws: Workspace) -> None:
    loaded = load(ws, SCL_COG)

    assert isinstance(loaded.summary, RasterSummary)
    assert loaded.summary.crs is not None and loaded.summary.crs.epsg == 32633
    assert loaded.summary.dims == {"y": 5490, "x": 5490}
    assert loaded.summary.resolution == (20.0, 20.0)
    assert ws.record(loaded.data_ref).fingerprint is None

    result = inspect(ws, loaded.data_ref)

    assert isinstance(result.summary, RasterSummary)
    assert result.summary.stats_method == "overview"
    stats = result.summary.bands[0].stats
    assert stats is not None and stats.min is not None and stats.max is not None
    assert stats.min in SCL_CLASSES and stats.max in SCL_CLASSES
    assert stats.valid_fraction > 0.5
