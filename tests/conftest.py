from pathlib import Path

import pytest

from loci import synthetic


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("fixtures")


@pytest.fixture(scope="session")
def ramp_tif(fixture_dir: Path) -> Path:
    return synthetic.write_ramp_raster(fixture_dir / "ramp.tif")


@pytest.fixture(scope="session")
def multiband_tif(fixture_dir: Path) -> Path:
    return synthetic.write_multiband_raster(fixture_dir / "multiband.tif")


@pytest.fixture(scope="session")
def bare_tif(fixture_dir: Path) -> Path:
    return synthetic.write_bare_raster(fixture_dir / "bare.tif")


@pytest.fixture(scope="session")
def overview_tif(fixture_dir: Path) -> Path:
    return synthetic.write_overview_raster(fixture_dir / "overviews.tif")


@pytest.fixture(scope="session", params=["gpkg", "geojson", "shp", "parquet"])
def polygons_path(request: pytest.FixtureRequest, fixture_dir: Path) -> Path:
    """The clean polygons in each supported vector format."""
    return synthetic.write_polygons(fixture_dir / f"polygons.{request.param}")


@pytest.fixture(scope="session")
def polygons_gpkg(fixture_dir: Path) -> Path:
    return synthetic.write_polygons(fixture_dir / "polygons_only.gpkg")


@pytest.fixture(scope="session")
def dirty_gpkg(fixture_dir: Path) -> Path:
    return synthetic.write_dirty_polygons(fixture_dir / "dirty.gpkg")


@pytest.fixture(scope="session")
def points_lonlat_csv(fixture_dir: Path) -> Path:
    return synthetic.write_points_lonlat_csv(fixture_dir / "points_lonlat.csv")


@pytest.fixture(scope="session")
def points_xy_csv(fixture_dir: Path) -> Path:
    return synthetic.write_points_xy_csv(fixture_dir / "points_xy.csv")


@pytest.fixture(scope="session")
def points_blank_csv(fixture_dir: Path) -> Path:
    return synthetic.write_points_blank_csv(fixture_dir / "points_blank.csv")


@pytest.fixture(scope="session")
def wide_csv(fixture_dir: Path) -> Path:
    return synthetic.write_wide_csv(fixture_dir / "wide.csv")
