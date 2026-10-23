"""Real data over the network: Sentinel-2 over Sydney, Australia, from Earth Search.

Deselected by default; run with `uv run pytest -m network`.
"""

from pathlib import Path

import pytest
from matplotlib.image import imread

from loci.models import RasterSummary
from loci.tools.inspect import inspect
from loci.tools.load import load
from loci.tools.preview import preview
from loci.tools.stac_search import stac_search
from loci.tools.validate import validate
from loci.workspace import Workspace

pytestmark = pytest.mark.network

# Sydney Harbour, from the CBD to the Heads, in degrees.
SYDNEY = [151.15, -33.90, 151.30, -33.80]
# A clear scene (0.1% cloud) of Sentinel-2 tile 56HLH, 18 June 2024.
SCENE = (
    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
    "56/H/LH/2024/6/S2B_56HLH_20240618_0_L2A/"
)
SCL_CLASSES = range(12)  # scene classification codes 0..11


def test_remote_cog_loads_lazily_and_inspects_from_an_overview(ws: Workspace) -> None:
    loaded = load(ws, SCENE + "SCL.tif")

    assert isinstance(loaded.summary, RasterSummary)
    assert loaded.summary.crs is not None and loaded.summary.crs.epsg == 32756
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


def test_validate_flags_mixed_resolutions_in_a_real_scene(ws: Workspace) -> None:
    red, scl = load(ws, SCENE + "B04.tif").data_ref, load(ws, SCENE + "SCL.tif").data_ref

    report = validate(ws, [red, scl])
    statuses = {c.check: c.status for c in report.checks}

    assert statuses["crs_consistent"] == "pass"
    assert statuses["extents_overlap"] == "pass"
    assert statuses["resolution_consistent"] == "warn"  # 10 m red band, 20 m classification


def test_stac_search_over_sydney(ws: Workspace) -> None:
    result = stac_search(ws, bbox=SYDNEY, start="2024-01-01", end="2024-12-31", max_cloud=20)

    assert result.items >= 30
    assert result.crs == {"EPSG:32756": result.items}
    assert result.grids == {"MGRS-56HLH": result.items}
    assert result.first is not None and result.last is not None
    assert "2024-01-01" <= result.first <= result.last <= "2024-12-31"
    assert {"red", "green", "blue", "nir", "scl"} <= set(result.bands)
    assert sum(m.items for m in result.by_month) == result.items


def test_stac_pixels_load_preview_and_validate_over_sydney(ws: Workspace) -> None:
    search = stac_search(ws, bbox=SYDNEY, start="2024-06-01", end="2024-06-30", max_cloud=20)

    loaded = load(ws, search.data_ref, bands=["red", "green", "blue"])

    assert isinstance(loaded.summary, RasterSummary)
    assert loaded.summary.crs is not None and loaded.summary.crs.epsg == 32756
    assert loaded.summary.dims["time"] >= 1
    assert loaded.summary.bands[0].scale == 0.0001

    picture = preview(ws, loaded.data_ref)
    image = imread(picture.path)

    assert picture.bands == ["red", "green", "blue"]
    assert picture.width <= 512 and image.shape == (picture.height, picture.width, 4)
    assert picture.time is not None and picture.time.startswith("2024-06")

    report = validate(ws, [loaded.data_ref])
    statuses = {c.check: c.status for c in report.checks}

    assert statuses["crs_defined"] == "pass"
    assert statuses["nodata_defined"] == "pass"
    assert statuses["valid_fraction"] == "pass"

    footprints = preview(ws, search.data_ref)

    assert Path(footprints.path).exists()
