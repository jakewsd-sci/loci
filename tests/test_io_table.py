from pathlib import Path

import pytest

from loci import synthetic as syn
from loci.errors import LoadError
from loci.io import Format, read
from loci.io.table import detect_coordinate_columns
from loci.models import ReadOptions

CSV = Format("vector", "csv")


def _write(path: Path, text: str) -> str:
    path.write_text(text)
    return str(path)


def test_lonlat_columns_are_detected_and_wgs84_is_assumed_with_a_warning(
    points_lonlat_csv: Path,
) -> None:
    result = read(str(points_lonlat_csv), CSV, ReadOptions())
    gdf = result.data

    assert len(gdf) == syn.POINTS_LONLAT_COUNT
    assert set(gdf.geom_type) == {"Point"}
    assert gdf.crs.to_epsg() == 4326
    assert result.crs_source == "assumed"
    assert result.options == ReadOptions(x_col="lon", y_col="lat", crs="EPSG:4326")
    assert any("assumed EPSG:4326" in w for w in result.warnings)


def test_xy_columns_leave_the_crs_undefined(points_xy_csv: Path) -> None:
    result = read(str(points_xy_csv), CSV, ReadOptions())

    assert result.data.crs is None
    assert result.crs_source is None
    assert any("CRS is undefined" in w for w in result.warnings)


def test_crs_argument_is_used_for_tables(points_xy_csv: Path) -> None:
    result = read(str(points_xy_csv), CSV, ReadOptions(crs="EPSG:32633"))

    assert result.data.crs.to_epsg() == 32633
    assert result.crs_source == "argument"
    assert result.warnings == []


def test_lonlat_names_with_out_of_range_values_are_not_assumed(tmp_path: Path) -> None:
    uri = _write(tmp_path / "utm.csv", "lon,lat\n500000,4100000\n500100,4099900\n")

    result = read(uri, CSV, ReadOptions())

    assert result.crs_source is None
    assert any("outside longitude/latitude ranges" in w for w in result.warnings)


def test_rows_with_bad_coordinates_are_kept_with_null_geometry(points_blank_csv: Path) -> None:
    result = read(str(points_blank_csv), CSV, ReadOptions())
    gdf = result.data

    assert len(gdf) == syn.POINTS_BLANK_ROWS
    assert int(gdf.geometry.isna().sum()) == syn.POINTS_BLANK_INVALID
    assert any(
        f"{syn.POINTS_BLANK_INVALID} of {syn.POINTS_BLANK_ROWS} rows" in w for w in result.warnings
    )


def test_explicit_columns_override_detection(tmp_path: Path) -> None:
    uri = _write(tmp_path / "odd.csv", "east_m,north_m,v\n500000,4100000,1\n")

    result = read(uri, CSV, ReadOptions(x_col="east_m", y_col="north_m", crs="EPSG:32633"))

    assert result.data.geometry.iloc[0].coords[0] == (500000.0, 4100000.0)


def test_both_coordinate_columns_must_be_given(tmp_path: Path) -> None:
    uri = _write(tmp_path / "a.csv", "lon,lat\n15,37\n")

    with pytest.raises(LoadError, match="both"):
        read(uri, CSV, ReadOptions(x_col="lon"))


def test_unknown_coordinate_column_is_an_error(tmp_path: Path) -> None:
    uri = _write(tmp_path / "a.csv", "lon,lat\n15,37\n")

    with pytest.raises(LoadError, match="not found"):
        read(uri, CSV, ReadOptions(x_col="lon", y_col="latitude"))


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        (["site", "Longitude", "Latitude"], ("Longitude", "Latitude")),
        (["X", "Y", "value"], ("X", "Y")),
        (["easting", "northing"], ("easting", "northing")),
    ],
)
def test_coordinate_columns_are_detected_case_insensitively(
    columns: list[str], expected: tuple[str, str]
) -> None:
    assert detect_coordinate_columns(columns) == expected


def test_ambiguous_coordinate_columns_are_an_error() -> None:
    with pytest.raises(LoadError, match="Ambiguous"):
        detect_coordinate_columns(["lon", "lat", "x", "y"])


def test_missing_coordinate_columns_are_an_error_with_a_hint() -> None:
    with pytest.raises(LoadError, match="No coordinate columns") as excinfo:
        detect_coordinate_columns(["site", "value"])

    assert excinfo.value.hint is not None
    assert "x_col=" in excinfo.value.hint
