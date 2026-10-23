from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.extent import check_extents_overlap

MakeLayer = Callable[..., Layer]


def _shifted_polygons(dx: float) -> Layer:
    gdf = syn.polygons()
    return Layer("parcels", gdf.set_geometry(gdf.translate(xoff=dx)))


def test_contained_extent_passes(make_layer: MakeLayer, ramp_tif: Path) -> None:
    # The 200 m polygon grid sits in the top-left corner of the 1000 x 800 m ramp.
    out = check_extents_overlap([make_layer(ramp_tif, "dem"), _shifted_polygons(0)])

    assert out.status == "pass"
    assert out.details["coverage"] == [
        {"smaller": "parcels", "larger": "dem", "fraction_inside": 1.0}
    ]


def test_partial_overlap_warns_with_the_fraction(make_layer: MakeLayer, ramp_tif: Path) -> None:
    # Shifted 900 m east, half of the 200 m wide grid hangs off the ramp's east edge.
    out = check_extents_overlap([make_layer(ramp_tif, "dem"), _shifted_polygons(900)])

    assert out.status == "warn"
    assert out.message == "Only 50% of the extent of parcels lies within the extent of dem."


def test_disjoint_extents_fail(make_layer: MakeLayer, ramp_tif: Path) -> None:
    out = check_extents_overlap([make_layer(ramp_tif, "dem"), _shifted_polygons(10_000)])

    assert out.status == "fail"
    assert "do not overlap" in out.message


def test_extents_are_compared_across_crss(
    make_layer: MakeLayer, ramp_tif: Path, fixture_dir: Path
) -> None:
    geojson = syn.write_polygons(fixture_dir / "polygons_wgs84.geojson")

    out = check_extents_overlap([make_layer(ramp_tif, "dem"), make_layer(geojson, "parcels")])

    assert out.status == "pass"
    coverage = out.details["coverage"]
    assert isinstance(coverage, list) and isinstance(coverage[0], dict)
    assert coverage[0]["fraction_inside"] == pytest.approx(1.0, abs=0.01)


def test_points_count_as_inside_or_outside() -> None:
    area = Layer("area", syn.polygons())
    inside = gpd.GeoDataFrame(geometry=gpd.points_from_xy([500_050], [4_099_950]), crs=syn.UTM33N)
    outside = gpd.GeoDataFrame(geometry=gpd.points_from_xy([0], [0]), crs=syn.UTM33N)

    assert check_extents_overlap([area, Layer("p", inside)]).status == "pass"
    assert check_extents_overlap([area, Layer("p", outside)]).status == "fail"


def test_empty_layer_or_missing_crs_cannot_be_compared(
    make_layer: MakeLayer, bare_tif: Path
) -> None:
    empty = Layer("empty", gpd.GeoDataFrame(geometry=[Polygon()], crs=syn.UTM33N))

    assert check_extents_overlap([Layer("a", syn.polygons()), empty]).status == "warn"
    assert (
        check_extents_overlap([Layer("a", syn.polygons()), make_layer(bare_tif)]).status == "warn"
    )
