"""validate end to end, as an agent would call it."""

from pathlib import Path

import dask.array
import pytest

from loci import synthetic as syn
from loci.errors import LociError, UnknownRefError
from loci.models import ValidationReport
from loci.tools.load import load
from loci.tools.validate import validate
from loci.workspace import Workspace

REPORT_BYTES_PER_INPUT = 1500


def _statuses(report: ValidationReport) -> dict[str, str]:
    return {c.check: c.status for c in report.checks}


def test_clean_raster_passes(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    report = validate(ws, [ref])

    assert report.status == "pass"
    assert report.data_refs == [ref]
    assert report.counts == {"pass": 3, "warn": 0, "fail": 0}


def test_raster_without_crs_or_nodata_fails(ws: Workspace, bare_tif: Path) -> None:
    report = validate(ws, [load(ws, str(bare_tif)).data_ref])

    assert report.status == "fail"
    assert _statuses(report) == {
        "crs_defined": "fail",
        "nodata_defined": "warn",
        "valid_fraction": "pass",
    }


def test_ndvi_range_is_checked_by_band_name(
    ws: Workspace, ndvi_tif: Path, bad_ndvi_tif: Path
) -> None:
    good = validate(ws, [load(ws, str(ndvi_tif)).data_ref])
    bad = validate(ws, [load(ws, str(bad_ndvi_tif)).data_ref])

    assert _statuses(good)["value_range"] == "pass"
    assert _statuses(bad)["value_range"] == "fail"
    assert bad.status == "fail"


def test_declared_variables_are_checked(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref  # values 0..99

    as_percent = validate(ws, [ref], variables={"band_1": "percent"})
    as_fraction = validate(ws, [ref], variables={"band_1": "fraction"})

    assert _statuses(as_percent)["value_range"] == "pass"
    assert _statuses(as_fraction)["value_range"] == "fail"


def test_inputs_to_combine_are_checked_together(
    ws: Workspace, ramp_tif: Path, coarse_tif: Path, polygons_gpkg: Path
) -> None:
    refs = [load(ws, str(p)).data_ref for p in (ramp_tif, coarse_tif, polygons_gpkg)]

    report = validate(ws, refs)
    statuses = _statuses(report)

    assert statuses["crs_consistent"] == "pass"
    assert statuses["extents_overlap"] == "pass"
    assert statuses["resolution_consistent"] == "warn"
    assert report.status == "warn"


def test_inputs_in_different_crss_fail(ws: Workspace, ramp_tif: Path, fixture_dir: Path) -> None:
    geojson = syn.write_polygons(fixture_dir / "polygons_validate.geojson")
    refs = [load(ws, str(ramp_tif)).data_ref, load(ws, str(geojson)).data_ref]

    statuses = _statuses(validate(ws, refs))

    assert statuses["crs_consistent"] == "fail"
    assert statuses["extents_overlap"] == "pass"  # compared after transforming the boxes


def test_dirty_vector_fails(ws: Workspace, dirty_gpkg: Path) -> None:
    report = validate(ws, [load(ws, str(dirty_gpkg)).data_ref])

    assert _statuses(report)["geometry_valid"] == "fail"


def test_points_with_an_assumed_crs_warn(ws: Workspace, points_lonlat_csv: Path) -> None:
    report = validate(ws, [load(ws, str(points_lonlat_csv)).data_ref])

    assert _statuses(report)["crs_defined"] == "warn"


def test_area_conservation_per_catchment(ws: Workspace, tmp_path: Path) -> None:
    catchments = syn.catchments()
    landuse = syn.landuse_by_catchment()
    catchments.to_file(tmp_path / "catchments.gpkg")
    landuse.to_file(tmp_path / "landuse.gpkg")
    landuse.drop(index=3).to_file(tmp_path / "landuse_lossy.gpkg")
    zones = load(ws, str(tmp_path / "catchments.gpkg")).data_ref
    exact = load(ws, str(tmp_path / "landuse.gpkg")).data_ref
    lossy = load(ws, str(tmp_path / "landuse_lossy.gpkg")).data_ref

    good = validate(ws, [exact], area_reference=zones, area_by="catchment")
    bad = validate(ws, [lossy], area_reference=zones, area_by="catchment")

    assert _statuses(good)["area_conserved"] == "pass"
    assert _statuses(bad)["area_conserved"] == "fail"
    assert "worst: 'east'" in bad.checks[-1].message


def test_vector_time_column(ws: Workspace, polygons_gpkg: Path) -> None:
    report = validate(ws, [load(ws, str(polygons_gpkg)).data_ref], time_column="surveyed")

    assert _statuses(report)["temporal_coverage"] == "pass"


def test_report_is_the_same_from_a_fresh_workspace(ws: Workspace, multiband_tif: Path) -> None:
    ref = load(ws, str(multiband_tif)).data_ref

    assert validate(Workspace(ws.root), [ref]) == validate(ws, [ref])


def test_bad_arguments_are_errors(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    with pytest.raises(LociError, match="at least one"):
        validate(ws, [])
    with pytest.raises(UnknownRefError):
        validate(ws, ["raster-00000000"])
    with pytest.raises(LociError, match="Unknown variable"):
        validate(ws, [ref], variables={"band_1": "greenness"})


def test_duplicate_refs_are_checked_once(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    report = validate(ws, [ref, ref])

    assert report.data_refs == [ref]
    assert "crs_consistent" not in _statuses(report)


def test_validate_leaves_cached_rasters_lazy(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref
    validate(ws, [ref])

    assert isinstance(ws.open(ref)["band_1"].data, dask.array.Array)


def test_reports_stay_compact(
    ws: Workspace, multiband_tif: Path, coarse_tif: Path, dirty_gpkg: Path
) -> None:
    refs = [load(ws, str(p)).data_ref for p in (multiband_tif, coarse_tif, dirty_gpkg)]

    report = validate(ws, refs)

    assert len(report.model_dump_json()) < REPORT_BYTES_PER_INPUT * len(refs)
