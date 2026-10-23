"""stac_search and STAC loading end to end, with the catalogue replaced by synthetic items."""

from pathlib import Path
from typing import Any

import pystac
import pytest
import xarray as xr

from loci import synthetic as syn
from loci.errors import LoadError, LociError
from loci.models import RasterSummary, VectorSummary
from loci.summary.raster import decimation_for
from loci.tools.inspect import inspect
from loci.tools.load import RASTER_LOAD_NOTE, load
from loci.tools.stac_search import stac_search
from loci.tools.validate import validate
from loci.workspace import Workspace

# The synthetic scenes' footprint (a 400 m square at ORIGIN in UTM 33N), in degrees.
AREA = [15.0, 37.042617, 15.004498, 37.046222]


class FakeCatalogue:
    """Stands in for search_items: returns the given items and remembers the request."""

    def __init__(self, items: list[pystac.Item], matched: int | None = None) -> None:
        self.items = items
        self.matched = matched if matched is not None else len(items)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> tuple[list[pystac.Item], int | None]:
        self.calls.append(kwargs)
        return self.items, self.matched


@pytest.fixture
def catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCatalogue:
    fake = FakeCatalogue(syn.stac_items(tmp_path / "scenes"))
    monkeypatch.setattr("loci.tools.stac_search.search_items", fake)
    return fake


def test_search_summarises_the_items(ws: Workspace, catalogue: FakeCatalogue) -> None:
    result = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31", max_cloud=80)

    assert result.data_ref.startswith("vector-")
    assert (result.items, result.matched, result.days) == (3, 3, 3)
    assert (result.first, result.last) == (syn.STAC_DATES[0], syn.STAC_DATES[-1])
    assert result.cloud_cover is not None
    assert (result.cloud_cover.min, result.cloud_cover.max) == (5.0, 60.0)
    assert result.crs == {"EPSG:32633": 3}
    assert [m.model_dump() for m in result.by_month] == [
        {"month": "2024-03", "items": 3, "days": 3, "median_cloud_cover": 30.0}
    ]
    assert [row.id for row in result.sample] == ["SYN_20240304", "SYN_20240314", "SYN_20240324"]
    assert result.bands == ["red", "nir", "scl"]
    assert result.warnings == []
    assert catalogue.calls[0]["datetime_range"] == "2024-03-01/2024-03-31"
    assert catalogue.calls[0]["max_cloud"] == 80
    assert catalogue.calls[0]["bbox"] == tuple(AREA)


def test_search_result_is_a_vector_layer(ws: Workspace, catalogue: FakeCatalogue) -> None:
    ref = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31").data_ref

    described = inspect(Workspace(ws.root), ref)
    report = validate(ws, [ref], time_column="datetime")

    assert isinstance(described.summary, VectorSummary)
    assert described.summary.feature_count == 3
    assert {c.check: c.status for c in report.checks}["temporal_coverage"] == "pass"
    assert (ws.stac_dir / f"{ref}.json").exists()


def test_items_load_as_a_raster_time_stack(ws: Workspace, catalogue: FakeCatalogue) -> None:
    ref = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31").data_ref

    loaded = load(ws, ref, bands=["red", "nir"])

    assert loaded.data_ref.startswith("raster-")
    assert isinstance(loaded.summary, RasterSummary)
    # The degree box reprojected to UTM is slightly larger than the scene, so a pixel or so of
    # nodata may border it.
    assert loaded.summary.dims["time"] == 3
    assert syn.STAC_SIZE <= loaded.summary.dims["y"] <= syn.STAC_SIZE + 2
    assert loaded.summary.time is not None and loaded.summary.time.steps == 3
    red = loaded.summary.bands[0]
    assert (red.name, red.scale, red.offset) == ("red", syn.STAC_SCALE, syn.STAC_OFFSET)
    assert RASTER_LOAD_NOTE in loaded.notes


def test_loaded_stack_inspects_the_same_from_a_fresh_workspace(
    ws: Workspace, catalogue: FakeCatalogue
) -> None:
    ref = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31").data_ref
    raster = load(ws, ref, bands=["red"]).data_ref

    fresh = inspect(Workspace(ws.root), raster)

    assert fresh == inspect(ws, raster)
    assert isinstance(fresh.summary, RasterSummary)
    stats = fresh.summary.bands[0].stats
    assert stats is not None
    assert (stats.min, stats.max) == (min(syn.STAC_RED), max(syn.STAC_RED))


def test_large_stacks_take_statistics_from_a_coarser_read(
    ws: Workspace, catalogue: FakeCatalogue
) -> None:
    ref = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31").data_ref
    raster = load(ws, ref, bands=["red"]).data_ref

    stack = ws.open(raster)
    assert isinstance(stack, xr.Dataset)

    result = inspect(ws, raster, max_pixels=1000)

    assert isinstance(result.summary, RasterSummary)
    assert result.summary.stats_method == "overview"
    assert result.summary.stats_decimation == decimation_for(stack, 1000) == 3


def test_validate_checks_scaled_reflectance(ws: Workspace, catalogue: FakeCatalogue) -> None:
    ref = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31").data_ref
    raster = load(ws, ref, bands=["red"]).data_ref

    report = validate(ws, [raster], variables={"red": "reflectance"})
    checks = {c.check: c for c in report.checks}

    assert checks["value_range"].status == "pass"
    assert checks["value_range"].details["max"] == pytest.approx(0.02)  # 1200 * 1e-4 - 0.1
    assert checks["temporal_coverage"].status == "pass"


def test_aoi_layer_becomes_the_search_geometry(
    ws: Workspace, catalogue: FakeCatalogue, polygons_gpkg: Path
) -> None:
    aoi = load(ws, str(polygons_gpkg)).data_ref

    result = stac_search(ws, aoi=aoi, start="2024-03-01", end="2024-03-31")
    call = catalogue.calls[0]

    assert call["bbox"] is None
    assert call["intersects"]["type"] == "Polygon"
    assert result.bbox[0] == pytest.approx(15.0)  # the grid's west edge is on 15 E
    assert result.bbox[3] < 37.05 and result.bbox[2] < 15.01


def test_truncated_and_empty_searches_warn(
    ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = syn.stac_items(tmp_path / "scenes")
    monkeypatch.setattr("loci.tools.stac_search.search_items", FakeCatalogue(items, matched=40))
    truncated = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31", limit=3)
    monkeypatch.setattr("loci.tools.stac_search.search_items", FakeCatalogue([]))
    empty = stac_search(ws, bbox=AREA, start="2024-03-01", end="2024-03-31")

    assert "Only the first 3 of 40" in truncated.warnings[0]
    assert empty.items == 0 and empty.first is None
    assert "No items matched" in empty.warnings[0]
    with pytest.raises(LoadError, match="found no items"):
        load(ws, empty.data_ref, bands=["red"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "either bbox= or aoi="),
        ({"bbox": AREA, "aoi": "vector-00000000"}, "either bbox= or aoi="),
        ({"bbox": [15.1, 37.0, 15.0, 37.1]}, "is not \\[west, south, east, north\\]"),
        ({"bbox": [15.0, 37.0, 15.1]}, "bbox is"),
        ({"bbox": AREA, "start": "2024-04-01"}, "is after end"),
        ({"bbox": AREA, "start": "March 2024"}, "ISO dates"),
        ({"bbox": AREA, "max_cloud": 120}, "percentage"),
    ],
)
def test_bad_search_arguments_are_errors(
    ws: Workspace, catalogue: FakeCatalogue, kwargs: dict[str, Any], message: str
) -> None:
    arguments = {"start": "2024-03-01", "end": "2024-03-31", **kwargs}

    with pytest.raises(LociError, match=message):
        stac_search(ws, **arguments)


def test_only_search_results_can_be_loaded_by_data_ref(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    with pytest.raises(LoadError, match="not a stac_search result"):
        load(ws, ref, bands=["red"])
