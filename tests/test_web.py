"""The web API behind the map interface, through Starlette's test client."""

import json
from pathlib import Path

import morecantile
import numpy as np
import pystac
import pytest
import rasterio
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from starlette.testclient import TestClient

from loci import synthetic as syn
from loci.tools.load import load
from loci.tools.stac_search import stac_search
from loci.web.app import create_app
from loci.workspace import Workspace

TMS = morecantile.tms.get("WebMercatorQuad")


@pytest.fixture
def client(ws: Workspace) -> TestClient:
    return TestClient(create_app(ws))


def _lonlat(x: float, y: float) -> tuple[float, float]:
    lon, lat = Transformer.from_crs(syn.UTM33N, "EPSG:4326", always_xy=True).transform(x, y)
    return lon, lat


def _ndvi_centre(column: int, row: int) -> tuple[float, float]:
    x0, y0 = syn.ORIGIN
    return _lonlat(x0 + (column + 0.5) * 10, y0 - (row + 0.5) * 10)


def test_layers_list_every_data_ref_with_what_the_map_needs(
    client: TestClient, ws: Workspace, ndvi_tif: Path, bare_tif: Path, polygons_path: Path
) -> None:
    ndvi = load(ws, str(ndvi_tif)).data_ref
    bare = load(ws, str(bare_tif)).data_ref
    parcels = load(ws, str(polygons_path)).data_ref

    listed = {layer["data_ref"]: layer for layer in client.get("/api/layers").json()}

    assert listed[ndvi]["name"] == ndvi_tif.name and listed[ndvi]["bands"] == ["ndvi"]
    assert listed[ndvi]["crs"] == "EPSG:32633" and listed[ndvi]["drawable"]
    assert listed[bare]["drawable"] is False and "No CRS" in listed[bare]["note"]
    assert listed[parcels]["kind"] == "vector" and listed[parcels]["bounds_wgs84"] is not None


def test_rasters_from_files_are_tiled_with_a_physical_stretch(
    client: TestClient, ws: Workspace, ndvi_tif: Path
) -> None:
    ref = load(ws, str(ndvi_tif)).data_ref

    shown = client.get(f"/api/layers/{ref}/display").json()
    lon, lat = _ndvi_centre(25, 25)
    inside = TMS.tile(lon, lat, 16)
    tile_url = shown["url"].format(z=16, x=inside.x, y=inside.y)
    far_url = shown["url"].format(z=16, x=0, y=0)

    assert (shown["mode"], shown["colormap"], shown["bands"]) == ("tiles", "viridis", ["ndvi"])
    low, high = shown["stretch"]
    assert syn.NDVI_MIN <= low < high <= syn.NDVI_MAX
    png = client.get(tile_url)
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert client.get(far_url).status_code == 204


def test_nan_pixels_are_transparent_even_without_a_declared_nodata(
    client: TestClient, ws: Workspace, tmp_path: Path
) -> None:
    values = np.ones((1, 20, 20), dtype="float32")
    values[0, :, :10] = np.nan  # the west half is missing
    path = tmp_path / "half.tif"
    profile = {"driver": "GTiff", "width": 20, "height": 20, "count": 1, "dtype": "float32"}
    transform = from_origin(*syn.ORIGIN, 10, 10)
    with rasterio.open(path, "w", **profile, crs=syn.UTM33N, transform=transform) as dst:
        dst.write(values)
    ref = load(ws, str(path)).data_ref
    shown = client.get(f"/api/layers/{ref}/display").json()
    west, east = (
        _lonlat(syn.ORIGIN[0] + 25, syn.ORIGIN[1] - 100),
        _lonlat(syn.ORIGIN[0] + 175, syn.ORIGIN[1] - 100),
    )

    def alpha(lon: float, lat: float) -> int:
        tile = TMS.tile(lon, lat, 18)
        png = client.get(shown["url"].format(z=18, x=tile.x, y=tile.y)).content
        with MemoryFile(png) as memory, memory.open() as image:
            row, col = image.index(*_pixel_in_tile(tile, lon, lat))
            return int(image.read(image.count)[row, col])

    assert alpha(*west) == 0 and alpha(*east) == 255


def _pixel_in_tile(tile: morecantile.Tile, lon: float, lat: float) -> tuple[float, float]:
    """The point's position in a 256-pixel tile image, as image (x, y) coordinates."""
    left, bottom, right, top = TMS.xy_bounds(tile)
    x, y = TMS.xy(lon, lat)
    return (x - left) / (right - left) * 256, (top - y) / (top - bottom) * 256


def test_stac_stacks_are_one_georeferenced_preview(
    client: TestClient, ws: Workspace, stac_scenes: list[pystac.Item]
) -> None:
    search = stac_search(
        ws, bbox=list(stac_scenes[0].bbox or []), start="2024-03-01", end="2024-03-31"
    )
    stack = load(ws, search.data_ref, bands=["red", "nir"]).data_ref

    shown = client.get(f"/api/layers/{stack}/display?bands=nir").json()
    picture = client.get(shown["url"])

    assert shown["mode"] == "image" and shown["note"].startswith("One preview picture")
    assert len(shown["coordinates"]) == 4 and shown["time"] is not None
    assert picture.status_code == 200 and picture.content.startswith(b"\x89PNG")


def test_values_at_a_point_come_from_the_data(
    client: TestClient, ws: Workspace, ndvi_tif: Path, polygons_path: Path
) -> None:
    ndvi = load(ws, str(ndvi_tif)).data_ref
    parcels = load(ws, str(polygons_path)).data_ref
    lon, lat = _ndvi_centre(7, 3)

    raster = client.get(f"/api/layers/{ndvi}/point", params={"lon": lon, "lat": lat}).json()
    vector = client.get(f"/api/layers/{parcels}/point", params={"lon": lon, "lat": lat}).json()
    outside = client.get(f"/api/layers/{ndvi}/point", params={"lon": 0, "lat": 0})

    (band,) = raster["bands"]
    assert band["values"][0] == pytest.approx(syn.NDVI_MIN + 7 / 49, abs=1e-6)
    assert raster["crs"] == "EPSG:32633"
    assert vector["features"][0]["landuse"] == syn.POLY_LANDUSE[0]
    assert outside.status_code == 400 and "outside" in outside.json()["error"]


def test_vectors_are_served_as_geojson_in_wgs84(
    client: TestClient, ws: Workspace, polygons_path: Path
) -> None:
    ref = load(ws, str(polygons_path)).data_ref

    shown = client.get(f"/api/layers/{ref}/display").json()
    collection = json.loads(client.get(shown["url"]).text)

    assert shown["mode"] == "geojson"
    assert len(collection["features"]) == syn.POLY_COUNT
    lon, lat = collection["features"][0]["geometry"]["coordinates"][0][0]
    assert 14 < lon < 16 and 36 < lat < 38


def test_detail_shows_lineage_and_validation_once_run(
    client: TestClient, ws: Workspace, ramp_tif: Path
) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    before = client.get(f"/api/layers/{ref}").json()
    report = client.post(f"/api/layers/{ref}/validate").json()
    after = client.get(f"/api/layers/{ref}").json()

    assert before["validation"] is None
    assert before["lineage"]["steps"][0]["produced_by"] == "load"
    assert report["status"] == after["validation"]["status"] == "pass"
    assert after["layer"]["validation"] == "pass"


def test_errors_are_json_with_hints(client: TestClient, ws: Workspace, bare_tif: Path) -> None:
    bare = load(ws, str(bare_tif)).data_ref

    missing = client.get("/api/layers/raster-00000000")
    unplaceable = client.get(f"/api/layers/{bare}/display")
    escape = client.get("/api/previews/..%2Fsecret.png")

    assert missing.status_code == 404 and missing.json()["hint"]
    assert unplaceable.status_code == 400 and "no CRS" in unplaceable.json()["error"]
    assert escape.status_code == 404


def test_the_built_web_app_is_served_at_the_root(ws: Workspace, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>loci</title>")

    page = TestClient(create_app(ws, dist=dist)).get("/")

    assert page.status_code == 200 and "<title>loci</title>" in page.text
