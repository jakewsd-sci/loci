import pytest

from loci.errors import UnsupportedFormatError
from loci.io.formats import Format, detect_format, is_remote


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("data/dem.tif", Format("raster", "geotiff")),
        ("data/DEM.TIFF", Format("raster", "geotiff")),
        ("https://example.com/s2/B04.tif?X-Amz-Signature=abc", Format("raster", "geotiff")),
        ("s3://bucket/cog.tif", Format("raster", "geotiff")),
        ("/vsizip/archive.zip/inner.tif", Format("raster", "geotiff")),
        ("landuse.gpkg", Format("vector", "gpkg")),
        ("catchments.geojson", Format("vector", "geojson")),
        ("catchments.json", Format("vector", "geojson")),
        ("rivers.shp", Format("vector", "shapefile")),
        ("parcels.parquet", Format("vector", "parquet")),
        ("samples.csv", Format("vector", "csv")),
    ],
)
def test_detect_format_from_suffix(uri: str, expected: Format) -> None:
    assert detect_format(uri) == expected


def test_kind_hands_unknown_suffixes_to_gdal_or_ogr() -> None:
    assert detect_format("scene.jp2", kind="raster") == Format("raster", "gdal")
    assert detect_format("roads.fgb", kind="vector") == Format("vector", "ogr")


def test_unknown_suffix_without_kind_is_an_error_with_a_hint() -> None:
    with pytest.raises(UnsupportedFormatError) as excinfo:
        detect_format("scene.jp2")

    assert excinfo.value.hint is not None
    assert "kind='raster'" in excinfo.value.hint


def test_kind_conflicting_with_suffix_is_an_error() -> None:
    with pytest.raises(UnsupportedFormatError, match="read as vector"):
        detect_format("landuse.gpkg", kind="raster")


def test_is_remote() -> None:
    assert is_remote("https://example.com/a.tif")
    assert is_remote("/vsicurl/https://example.com/a.tif")
    assert not is_remote("/data/a.tif")
    assert not is_remote("relative/a.tif")
