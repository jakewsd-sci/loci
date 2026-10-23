from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from loci import synthetic as syn
from loci.io import detect_format, read
from loci.models import ReadOptions
from loci.summary.common import LIST_BUDGET_BYTES
from loci.summary.vector import (
    MAX_ATTRIBUTES,
    attribute_summary,
    summarise_vector,
    vector_notes,
    vector_warnings,
)


def _read(path: Path) -> gpd.GeoDataFrame:
    data = read(str(path), detect_format(str(path)), ReadOptions()).data
    assert isinstance(data, gpd.GeoDataFrame)
    return data


def test_clean_polygons(polygons_gpkg: Path) -> None:
    summary = summarise_vector(_read(polygons_gpkg), crs_source="file")
    x0, y0 = syn.ORIGIN

    assert summary.crs is not None and summary.crs.epsg == 32633
    assert summary.feature_count == syn.POLY_COUNT
    assert summary.geometry_types == {"Polygon": syn.POLY_COUNT}
    assert (summary.null_geometries, summary.empty_geometries) == (0, 0)
    assert summary.invalid_geometries.count == 0
    assert summary.extent is not None
    assert summary.extent.bounds == (x0, y0 - 2 * syn.POLY_SIZE, x0 + 2 * syn.POLY_SIZE, y0)
    assert summary.points is None
    assert vector_warnings(summary) == []


def test_attribute_statistics_by_type(polygons_gpkg: Path) -> None:
    attrs = {a.name: a for a in summarise_vector(_read(polygons_gpkg)).attributes}

    assert attrs["id"].numeric is not None
    assert (attrs["id"].numeric.min, attrs["id"].numeric.max) == (1, syn.POLY_COUNT)
    assert attrs["score"].numeric is not None
    assert attrs["score"].numeric.mean == syn.POLY_SCORE_MEAN
    assert attrs["landuse"].categorical is not None
    assert attrs["landuse"].categorical.n_unique == 3
    assert attrs["landuse"].categorical.top == [("forest", 2), ("urban", 1), ("water", 1)]
    assert attrs["surveyed"].datetime is not None
    assert (attrs["surveyed"].datetime.min, attrs["surveyed"].datetime.max) == (
        syn.POLY_SURVEYED[0],
        syn.POLY_SURVEYED[-1],
    )


def test_dirty_polygons_are_counted_with_reasons(dirty_gpkg: Path) -> None:
    summary = summarise_vector(_read(dirty_gpkg))

    assert summary.feature_count == 4
    assert summary.geometry_types == {"Polygon": 2}
    assert (summary.null_geometries, summary.empty_geometries) == (1, 1)
    assert summary.invalid_geometries.count == 1
    example = summary.invalid_geometries.examples[0]
    assert example.row == syn.DIRTY_INVALID_ROW
    assert example.reason.startswith("Self-intersection")
    assert len(vector_warnings(summary)) == 3


def test_points_summary(points_lonlat_csv: Path) -> None:
    summary = summarise_vector(_read(points_lonlat_csv), crs_source="assumed")

    assert summary.geometry_types == {"Point": syn.POINTS_LONLAT_COUNT}
    assert summary.crs_source == "assumed"
    assert summary.points is not None
    assert (summary.points.duplicate_locations, summary.points.has_z) == (0, False)


def test_duplicate_point_locations_are_noted() -> None:
    gdf = gpd.GeoDataFrame(
        {"v": [1, 2, 3]}, geometry=[Point(0, 0), Point(0, 0), Point(1, 1)], crs="EPSG:32633"
    )

    summary = summarise_vector(gdf)

    assert summary.points is not None
    assert summary.points.duplicate_locations == 1
    assert any("share a location" in n for n in vector_notes(summary))


def test_attribute_list_is_capped(wide_csv: Path) -> None:
    gdf = _read(wide_csv)
    columns = len(gdf.columns) - 1  # minus geometry

    summary = summarise_vector(gdf)
    listed_bytes = sum(len(a.model_dump_json()) for a in summary.attributes)

    assert 0 < len(summary.attributes) <= MAX_ATTRIBUTES
    assert listed_bytes <= LIST_BUDGET_BYTES
    assert summary.attributes_omitted == columns - len(summary.attributes)
    assert any("more attributes" in n for n in vector_notes(summary))


def test_long_text_values_are_truncated() -> None:
    summary = attribute_summary("notes", pd.Series(["a" * 100, "a" * 100, "b"]))

    assert summary.categorical is not None
    value, count = summary.categorical.top[0]
    assert (len(value), count) == (40, 2)


def test_bool_and_all_null_columns() -> None:
    flags = attribute_summary("flag", pd.Series([True, False, True]))
    nulls = attribute_summary("depth", pd.Series([None, None], dtype="float64"))

    assert flags.categorical is not None
    assert flags.categorical.top == [("True", 2), ("False", 1)]
    assert nulls.null_count == 2
    assert nulls.numeric is not None and nulls.numeric.mean is None


def test_empty_layer_has_no_extent() -> None:
    gdf = gpd.GeoDataFrame({"v": []}, geometry=[], crs="EPSG:32633")

    summary = summarise_vector(gdf)

    assert summary.feature_count == 0
    assert summary.extent is None
    assert summary.geometry_types == {}
