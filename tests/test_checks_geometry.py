from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.geometry import check_area_conserved, check_geometry_valid, feature_areas

MakeLayer = Callable[..., Layer]


def test_clean_geometries_pass(make_layer: MakeLayer, polygons_gpkg: Path) -> None:
    out = check_geometry_valid(make_layer(polygons_gpkg))

    assert out.status == "pass"
    assert out.message == f"All {syn.POLY_COUNT} geometries are valid and non-empty."


def test_invalid_geometry_fails_with_row_and_reason(
    make_layer: MakeLayer, dirty_gpkg: Path
) -> None:
    out = check_geometry_valid(make_layer(dirty_gpkg))

    assert out.status == "fail"
    assert out.message.startswith("1 of 4 features has an invalid geometry (first: row 1, Self")
    assert out.details["empty"] == 1 and out.details["missing"] == 1


def test_empty_geometries_warn() -> None:
    gdf = syn.polygons()
    gdf.loc[2, "geometry"] = Polygon()

    out = check_geometry_valid(Layer("p", gdf))

    assert out.status == "warn"
    assert out.message == "1 of 4 features has an empty or missing geometry."


def test_empty_layer_warns() -> None:
    out = check_geometry_valid(Layer("p", gpd.GeoDataFrame(geometry=[], crs=syn.UTM33N)))

    assert out.status == "warn"


def test_areas_are_in_square_metres_whatever_the_crs() -> None:
    utm = syn.polygons()
    feet = utm.to_crs("+proj=utm +zone=33 +datum=WGS84 +units=us-ft")

    assert feature_areas(utm) == pytest.approx(syn.POLY_AREA)
    assert feature_areas(feet) == pytest.approx(syn.POLY_AREA, rel=1e-9)
    # Geodesic areas differ from UTM's planar ones by its 0.9996 scale factor (~0.08% in area).
    assert feature_areas(utm.to_crs("EPSG:4326")) == pytest.approx(syn.POLY_AREA, rel=1e-3)


def test_dissolve_conserves_area() -> None:
    parcels = syn.polygons()
    dissolved = parcels.dissolve()

    out = check_area_conserved(Layer("dissolved", dissolved), Layer("parcels", parcels))

    assert out.status == "pass"
    assert out.message == "Total area of dissolved (4 ha) matches parcels within 0.01%."


def test_lost_piece_fails() -> None:
    parcels = syn.polygons()

    out = check_area_conserved(Layer("overlay", parcels.iloc[:3]), Layer("parcels", parcels))

    assert out.status == "fail"
    assert (
        out.message
        == "Total area of overlay is 3 ha but parcels has 4 ha: 25% apart (tolerance 0.01%)."
    )


def test_area_per_group_passes_for_an_exact_overlay() -> None:
    out = check_area_conserved(
        Layer("landuse", syn.landuse_by_catchment()),
        Layer("catchments", syn.catchments()),
        by="catchment",
    )

    assert out.status == "pass"
    assert out.details["groups"] == len(syn.CATCHMENTS)


def test_area_per_group_names_the_worst_group() -> None:
    pieces = syn.landuse_by_catchment().drop(index=3)  # the east catchment loses a square

    out = check_area_conserved(
        Layer("landuse", pieces), Layer("catchments", syn.catchments()), by="catchment"
    )

    assert out.status == "fail"
    assert "for 1 of 2 groups of 'catchment' (worst: 'east', 1 ha against 2 ha)" in out.message
    assert out.details["worst"] == {
        "group": "east",
        "output_m2": syn.POLY_AREA,
        "reference_m2": syn.CATCHMENT_AREA,
        "relative_difference": 0.5,
    }


def test_group_missing_from_the_output_fails() -> None:
    pieces = syn.landuse_by_catchment().query("catchment == 'west'")

    out = check_area_conserved(
        Layer("landuse", pieces), Layer("catchments", syn.catchments()), by="catchment"
    )

    assert out.status == "fail"
    assert out.details["groups_outside"] == 1


def test_areas_in_different_crss_are_not_compared() -> None:
    parcels = syn.polygons()

    out = check_area_conserved(Layer("a", parcels.to_crs("EPSG:4326")), Layer("b", parcels))

    assert out.status == "fail"
    assert "do not share a CRS" in out.message
