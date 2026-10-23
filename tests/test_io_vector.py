from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from loci import synthetic as syn
from loci.errors import LoadError
from loci.io import Format, detect_format, read
from loci.models import ReadOptions

GPKG = Format("vector", "gpkg")


def test_every_vector_format_reads_the_same_features(polygons_path: Path) -> None:
    result = read(str(polygons_path), detect_format(str(polygons_path)), ReadOptions())
    gdf = result.data
    expected_epsg = 4326 if polygons_path.suffix == ".geojson" else 32633

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == syn.POLY_COUNT
    assert gdf.crs.to_epsg() == expected_epsg
    assert sorted(gdf["landuse"]) == sorted(syn.POLY_LANDUSE)
    assert result.crs_source == "file"
    assert result.warnings == []


def _two_layer_gpkg(path: Path) -> Path:
    gdf = syn.polygons()
    gdf.to_file(path, layer="landuse")
    gdf.iloc[:2].to_file(path, layer="catchments")
    return path


def test_first_of_several_layers_is_read_with_a_warning(tmp_path: Path) -> None:
    path = _two_layer_gpkg(tmp_path / "two.gpkg")

    result = read(str(path), GPKG, ReadOptions())

    assert len(result.data) == syn.POLY_COUNT
    assert result.options.layer == "landuse"
    assert any("2 layers" in w and "layer=" in w for w in result.warnings)


def test_requested_layer_is_read(tmp_path: Path) -> None:
    path = _two_layer_gpkg(tmp_path / "two.gpkg")

    result = read(str(path), GPKG, ReadOptions(layer="catchments"))

    assert len(result.data) == 2
    assert result.warnings == []


def test_unknown_layer_lists_the_available_ones(tmp_path: Path) -> None:
    path = _two_layer_gpkg(tmp_path / "two.gpkg")

    with pytest.raises(LoadError, match="available layers"):
        read(str(path), GPKG, ReadOptions(layer="rivers"))


@pytest.mark.filterwarnings("ignore:'crs' was not provided:UserWarning")
def test_vector_without_crs_is_reported_and_can_be_assigned(tmp_path: Path) -> None:
    path = tmp_path / "nocrs.gpkg"
    syn.polygons().set_crs(None, allow_override=True).to_file(path)

    unknown = read(str(path), GPKG, ReadOptions())
    assigned = read(str(path), GPKG, ReadOptions(crs="EPSG:32633"))

    assert unknown.crs_source is None
    assert any("declares no CRS" in w for w in unknown.warnings)
    assert assigned.data.crs.to_epsg() == 32633
    assert assigned.crs_source == "argument"


def test_crs_argument_never_overrides_a_declared_vector_crs(polygons_gpkg: Path) -> None:
    with pytest.raises(LoadError, match="never reprojects"):
        read(str(polygons_gpkg), GPKG, ReadOptions(crs="EPSG:4326"))


def test_plain_parquet_is_read_as_a_coordinate_table(tmp_path: Path) -> None:
    path = tmp_path / "samples.parquet"
    syn.points_lonlat().to_parquet(path)

    result = read(str(path), Format("vector", "parquet"), ReadOptions())

    assert len(result.data) == syn.POINTS_LONLAT_COUNT
    assert (result.options.x_col, result.options.y_col) == ("lon", "lat")
    assert result.crs_source == "assumed"


def test_layer_is_rejected_for_formats_without_layers(polygons_gpkg: Path, tmp_path: Path) -> None:
    path = tmp_path / "samples.parquet"
    pd.DataFrame({"lon": [15.0], "lat": [37.0]}).to_parquet(path)

    with pytest.raises(LoadError, match="layer="):
        read(str(path), Format("vector", "parquet"), ReadOptions(layer="a"))


def test_missing_vector_file_is_a_load_error(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="Cannot open"):
        read(str(tmp_path / "missing.gpkg"), GPKG, ReadOptions())
