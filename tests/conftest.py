import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pystac
import pytest

from loci import synthetic
from loci.checks.core import Layer
from loci.io import detect_format, read
from loci.models import ReadOptions
from loci.web.providers import CLAUDE_GATEWAYS, PROVIDERS
from loci.workspace import Workspace

MakeLayer = Callable[..., Layer]


@pytest.fixture(autouse=True)
def _no_user_settings(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests never read the developer's own loci settings or other providers' keys. Claude
    counts as set up, as a scripted agent stands in for it; real Claude sessions (-m claude)
    keep whatever Anthropic credentials the environment has."""
    monkeypatch.setenv("LOCI_CONFIG", str(tmp_path_factory.getbasetemp() / "no-config.toml"))
    for provider in PROVIDERS:
        if provider.id != "claude":
            for key in provider.keys:
                monkeypatch.delenv(key, raising=False)
    if not any(os.environ.get(n) for n in ("ANTHROPIC_API_KEY", *CLAUDE_GATEWAYS)):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-placeholder-for-tests")


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / ".loci")


@pytest.fixture
def make_layer() -> MakeLayer:
    """Build a check Layer from a file: make_layer(path, name="x", crs=..., x_col=...)."""

    def make(path: Path, name: str = "x", **options: Any) -> Layer:
        loaded = read(str(path), detect_format(str(path)), ReadOptions(**options))
        return Layer(name, loaded.data, loaded.crs_source)

    return make


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


@pytest.fixture(scope="session")
def ndvi_tif(fixture_dir: Path) -> Path:
    return synthetic.write_ndvi_raster(fixture_dir / "ndvi.tif")


@pytest.fixture(scope="session")
def bad_ndvi_tif(fixture_dir: Path) -> Path:
    return synthetic.write_ndvi_raster(fixture_dir / "ndvi_bad.tif", out_of_range=True)


@pytest.fixture(scope="session")
def coarse_tif(fixture_dir: Path) -> Path:
    return synthetic.write_coarse_raster(fixture_dir / "coarse.tif")


@pytest.fixture(scope="session")
def shifted_tif(fixture_dir: Path) -> Path:
    return synthetic.write_shifted_raster(fixture_dir / "shifted.tif")


@pytest.fixture
def stac_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[pystac.Item]:
    """The synthetic STAC scenes, served in place of a catalogue to stac_search."""
    items = synthetic.stac_items(tmp_path / "scenes")

    def search(**_: Any) -> tuple[list[pystac.Item], int]:
        return items, len(items)

    monkeypatch.setattr("loci.tools.stac_search.search_items", search)
    return items
