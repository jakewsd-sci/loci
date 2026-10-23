"""load and inspect end to end, as an agent would call them."""

import shutil
from pathlib import Path

import pytest

from loci import synthetic as syn
from loci.errors import LoadError, UnsupportedFormatError
from loci.models import RasterSummary, VectorSummary
from loci.tools.inspect import SOURCE_CHANGED, inspect
from loci.tools.load import RASTER_LOAD_NOTE, load
from loci.workspace import Workspace

SUMMARY_BUDGET_BYTES = 4096

ALL_FIXTURES = [
    "ramp_tif",
    "multiband_tif",
    "bare_tif",
    "overview_tif",
    "polygons_gpkg",
    "dirty_gpkg",
    "points_lonlat_csv",
    "points_xy_csv",
    "points_blank_csv",
    "wide_csv",
]


def test_load_raster_returns_a_ref_and_a_header(ws: Workspace, ramp_tif: Path) -> None:
    result = load(ws, str(ramp_tif))

    assert result.kind == "raster"
    assert result.data_ref.startswith("raster-")
    assert isinstance(result.summary, RasterSummary)
    assert result.summary.bands[0].stats is None
    assert RASTER_LOAD_NOTE in result.notes
    assert (ws.refs_dir / f"{result.data_ref}.json").exists()


def test_inspect_raster_adds_statistics(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    result = inspect(ws, ref)

    assert isinstance(result.summary, RasterSummary)
    stats = result.summary.bands[0].stats
    assert stats is not None
    assert stats.mean == syn.RAMP_MEAN
    assert result.summary.stats_method == "exact"
    assert RASTER_LOAD_NOTE not in result.notes


def test_inspect_from_a_fresh_workspace_matches(ws: Workspace, multiband_tif: Path) -> None:
    ref = load(ws, str(multiband_tif)).data_ref

    assert inspect(Workspace(ws.root), ref) == inspect(ws, ref)


def test_load_vector_is_summarised_completely(ws: Workspace, polygons_gpkg: Path) -> None:
    result = load(ws, str(polygons_gpkg))

    assert result.data_ref.startswith("vector-")
    assert isinstance(result.summary, VectorSummary)
    assert result.summary.feature_count == syn.POLY_COUNT
    assert result.warnings == []
    assert inspect(ws, result.data_ref) == result


def test_load_points_flags_the_assumed_crs(ws: Workspace, points_lonlat_csv: Path) -> None:
    result = load(ws, str(points_lonlat_csv))

    assert isinstance(result.summary, VectorSummary)
    assert result.summary.crs_source == "assumed"
    assert any("assumed EPSG:4326" in w for w in result.warnings)
    assert inspect(Workspace(ws.root), result.data_ref).warnings == result.warnings


def test_load_passes_options_through(ws: Workspace, points_xy_csv: Path) -> None:
    result = load(ws, str(points_xy_csv), crs="EPSG:32633")

    assert isinstance(result.summary, VectorSummary)
    assert result.summary.crs is not None and result.summary.crs.epsg == 32633
    assert result.summary.crs_source == "argument"
    assert result.warnings == []


def test_inspect_uses_overviews_for_large_rasters(ws: Workspace, overview_tif: Path) -> None:
    ref = load(ws, str(overview_tif)).data_ref

    # 512 x 512 = 262,144 pixels; within 20,000 needs 1 in 4 along each axis.
    result = inspect(ws, ref, max_pixels=20_000)

    assert isinstance(result.summary, RasterSummary)
    assert (result.summary.stats_method, result.summary.stats_decimation) == ("overview", 4)
    stats = result.summary.bands[0].stats
    assert stats is not None
    assert stats.mean == pytest.approx(syn.OV_MEAN)
    assert any("overview" in n for n in result.notes)


def test_inspect_falls_back_to_strided_without_overviews(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    result = inspect(ws, ref, max_pixels=1000)

    assert isinstance(result.summary, RasterSummary)
    assert (result.summary.stats_method, result.summary.stats_decimation) == ("strided", 3)


def test_inspect_without_stats_is_header_only(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    result = inspect(ws, ref, stats=False)

    assert isinstance(result.summary, RasterSummary)
    assert result.summary.stats_method is None


def test_inspect_warns_when_the_source_changed(
    ws: Workspace, ramp_tif: Path, tmp_path: Path
) -> None:
    copy = shutil.copy(ramp_tif, tmp_path / "copy.tif")
    ref = load(ws, str(copy)).data_ref
    syn.write_multiband_raster(Path(copy))

    assert SOURCE_CHANGED in inspect(Workspace(ws.root), ref).warnings


def test_relative_paths_are_stored_absolute(
    ws: Workspace, ramp_tif: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ramp_tif.parent)

    ref = load(ws, ramp_tif.name).data_ref

    assert ws.record(ref).source.uri == str(ramp_tif.resolve())


def test_missing_file_is_reported_with_the_working_directory(ws: Workspace) -> None:
    with pytest.raises(LoadError, match="No such file") as excinfo:
        load(ws, "nowhere/missing.tif")

    assert excinfo.value.hint is not None
    assert "resolved from" in excinfo.value.hint


def test_unsupported_format_is_reported(ws: Workspace, tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not data")

    with pytest.raises(UnsupportedFormatError):
        load(ws, str(path))


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_summaries_stay_compact(
    ws: Workspace, fixture: str, request: pytest.FixtureRequest
) -> None:
    path: Path = request.getfixturevalue(fixture)
    ref = load(ws, str(path)).data_ref

    result = inspect(ws, ref)

    assert len(result.model_dump_json()) < SUMMARY_BUDGET_BYTES
