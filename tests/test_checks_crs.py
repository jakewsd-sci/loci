from collections.abc import Callable
from pathlib import Path

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.crs import check_crs_consistent, check_crs_defined

MakeLayer = Callable[..., Layer]


def test_declared_crs_passes(make_layer: MakeLayer, ramp_tif: Path) -> None:
    out = check_crs_defined(make_layer(ramp_tif, "dem"))

    assert out.status == "pass"
    assert out.details == {"crs": "EPSG:32633", "crs_source": "file"}
    assert "declared by the source" in out.message


def test_crs_given_at_load_passes(make_layer: MakeLayer, bare_tif: Path) -> None:
    out = check_crs_defined(make_layer(bare_tif, crs="EPSG:32633"))

    assert out.status == "pass"
    assert "given at load" in out.message


def test_missing_crs_fails(make_layer: MakeLayer, bare_tif: Path) -> None:
    out = check_crs_defined(make_layer(bare_tif, "bare"))

    assert out.status == "fail"
    assert out.data_refs == ["bare"]
    assert out.hint is not None and "crs=" in out.hint


def test_assumed_crs_warns(make_layer: MakeLayer, points_lonlat_csv: Path) -> None:
    out = check_crs_defined(make_layer(points_lonlat_csv))

    assert out.status == "warn"
    assert "assumed" in out.message


def test_same_crs_across_inputs_passes(
    make_layer: MakeLayer, ramp_tif: Path, polygons_gpkg: Path
) -> None:
    out = check_crs_consistent([make_layer(ramp_tif, "dem"), make_layer(polygons_gpkg, "parcels")])

    assert out.status == "pass"
    assert out.details == {"crs": {"dem": "EPSG:32633", "parcels": "EPSG:32633"}}


def test_different_crs_across_inputs_fails(
    make_layer: MakeLayer, ramp_tif: Path, fixture_dir: Path
) -> None:
    geojson = syn.write_polygons(fixture_dir / "polygons_wgs84.geojson")

    out = check_crs_consistent([make_layer(ramp_tif, "dem"), make_layer(geojson, "parcels")])

    assert out.status == "fail"
    assert "EPSG:32633 (dem)" in out.message and "EPSG:4326 (parcels)" in out.message
    assert out.hint is not None and "Reproject" in out.hint


def test_missing_crs_makes_consistency_unknown(
    make_layer: MakeLayer, ramp_tif: Path, bare_tif: Path
) -> None:
    out = check_crs_consistent([make_layer(ramp_tif, "dem"), make_layer(bare_tif, "bare")])

    assert out.status == "warn"
    assert "bare has none" in out.message
