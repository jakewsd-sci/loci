"""Real data over the network: Sentinel-2 over Sydney, Australia, from Earth Search.

Deselected by default; run with `uv run pytest -m network`.
"""

from pathlib import Path

import pytest
from matplotlib.image import imread

from loci.models import RasterSummary
from loci.tools.inspect import inspect
from loci.tools.lineage import lineage
from loci.tools.load import load
from loci.tools.preview import preview
from loci.tools.run import run
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
EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"

NDVI = """
from loci import script

stack = script.read("stack")
red, nir = stack["red"], stack["nir"]
script.save(((nir - red) / (nir + red)).median("time").rename("ndvi"), "ndvi_median.tif")
"""


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


def test_drop_offset_gives_non_negative_reflectance_over_sydney(ws: Workspace) -> None:
    # Earth Search marks these scenes as already offset, yet declares offset -0.1 (D-026). Unlike
    # the tests above, this one tracks that upstream quirk on purpose: if Earth Search corrects
    # its metadata, it fails, and drop_offset is no longer needed for these scenes.
    search = stac_search(ws, bbox=SYDNEY, start="2024-06-01", end="2024-06-30", max_cloud=20)
    declared = load(ws, search.data_ref, bands=["red"])
    dropped = load(ws, search.data_ref, bands=["red"], drop_offset=True)

    def red_min(ref: str) -> float:
        report = validate(ws, [ref], variables={"red": "reflectance"})
        check = next(c for c in report.checks if c.check == "value_range")
        low = check.details["min"]
        assert isinstance(low, float)
        return low

    assert any("counted twice" in w for w in declared.warnings)
    assert red_min(declared.data_ref) < 0  # impossible reflectance with the declared offset
    assert red_min(dropped.data_ref) >= 0
    assert any("not applied" in n for n in dropped.notes)


def test_ndvi_run_over_sydney_traces_back_to_earth_search(ws: Workspace) -> None:
    search = stac_search(ws, bbox=SYDNEY, start="2024-06-01", end="2024-06-30", max_cloud=20)
    stack = load(ws, search.data_ref, bands=["red", "nir"], drop_offset=True)

    result = run(ws, code=NDVI, inputs={"stack": stack.data_ref})
    output = result.outputs[0]
    assert (result.status, result.warnings) == ("ok", [])
    assert output.validation == "pass"  # includes NDVI within [-1, 1]
    assert output.data_ref is not None

    bands = inspect(ws, output.data_ref).summary
    assert isinstance(bands, RasterSummary)
    stats = bands.bands[0].stats
    assert stats is not None and stats.mean is not None
    # Harbour, ocean, suburbs and parks: open water is strongly negative, vegetation positive.
    assert 0.05 < stats.mean < 0.35

    traced = lineage(ws, output.data_ref)
    assert [s.produced_by for s in traced.steps] == ["run", "load", "stac_search"]
    assert traced.original_sources == [EARTH_SEARCH]
    assert traced.stac_item_count == search.items
