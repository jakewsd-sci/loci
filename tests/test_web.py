"""The web API behind the map interface, through Starlette's test client."""

import json
from pathlib import Path

import morecantile
import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from starlette.testclient import TestClient

from loci import synthetic as syn
from loci.demo import write_demo
from loci.tools.load import load
from loci.tools.stac_search import stac_search
from loci.web.app import DEFAULT_PORT, create_app
from loci.workspace import Workspace

LOCAL_URL = f"http://127.0.0.1:{DEFAULT_PORT}"  # the app answers to its own names

TMS = morecantile.tms.get("WebMercatorQuad")


@pytest.fixture
def client(ws: Workspace) -> TestClient:
    return TestClient(create_app(ws), base_url=LOCAL_URL)


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
    url = client.get(f"/api/layers/{ref}/display").json()["url"]
    west = _lonlat(syn.ORIGIN[0] + 25, syn.ORIGIN[1] - 100)
    east = _lonlat(syn.ORIGIN[0] + 175, syn.ORIGIN[1] - 100)

    assert _rgba(client, url, *west)[3] == 0 and _rgba(client, url, *east)[3] == 255


def _rgba(client: TestClient, url: str, lon: float, lat: float, z: int = 18) -> tuple[int, ...]:
    """The colour drawn at a point, from the tile at zoom z that holds it."""
    tile = TMS.tile(lon, lat, z)
    response = client.get(url.format(z=z, x=tile.x, y=tile.y))
    assert response.status_code == 200, response.text
    left, bottom, right, top = TMS.xy_bounds(tile)
    x, y = TMS.xy(lon, lat)
    col = int((x - left) / (right - left) * 256)
    row = int((top - y) / (top - bottom) * 256)
    with MemoryFile(response.content) as memory, memory.open() as image:
        pixels = image.read()
    return tuple(int(band[row, col]) for band in pixels)


CLOUD_ROWS = 10  # the top rows of the last scene are cloud


@pytest.fixture
def cloudy_stack(ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """red, nir and scl of the three synthetic scenes, the last with cloud along its top."""
    items = syn.stac_items(tmp_path / "scenes", cloud_rows=[0, 0, CLOUD_ROWS])
    monkeypatch.setattr("loci.tools.stac_search.search_items", lambda **_: (items, len(items)))
    search = stac_search(ws, bbox=list(items[0].bbox or []), start="2024-03-01", end="2024-03-31")
    return load(ws, search.data_ref, bands=["red", "nir", "scl"], resolution=10).data_ref


def _stac_point(column: int, row: int) -> tuple[float, float]:
    x0, y0 = syn.ORIGIN
    return _lonlat(x0 + (column + 0.5) * syn.STAC_RES, y0 - (row + 0.5) * syn.STAC_RES)


def test_time_stacks_are_tiled_one_step_at_a_time(client: TestClient, cloudy_stack: str) -> None:
    shown = client.get(f"/api/layers/{cloudy_stack}/display?bands=nir").json()
    cloudy, clear = _stac_point(5, 2), _stac_point(5, 30)

    def at(step: int, point: tuple[float, float]) -> tuple[int, ...]:
        return _rgba(client, shown["url"].replace(f"step={shown['step']}", f"step={step}"), *point)

    assert shown["mode"] == "tiles" and shown["step"] == syn.STAC_CLEAREST
    assert [t[:10] for t in shown["times"]] == list(syn.STAC_DATES)
    # One stretch for every date: the cloud on the last date shows as a different colour.
    assert at(2, cloudy) != at(syn.STAC_CLEAREST, cloudy)
    assert at(2, clear) == at(syn.STAC_CLEAREST, clear)
    assert at(2, cloudy)[3] == 255
    outside = _lonlat(syn.ORIGIN[0] - 15, syn.ORIGIN[1] - 15)  # just west of the scenes
    assert at(2, outside)[3] == 0
    wrong = client.get(shown["url"].format(z=18, x=0, y=0).replace("step=1", "step=7"))
    assert wrong.status_code == 400 and "step" in wrong.json()["error"]


def test_large_rasters_are_decimated_to_fit_and_say_so(
    client: TestClient, cloudy_stack: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("loci.web.layers.MAX_FRAME_PIXELS", syn.STAC_SIZE**2 // 4)

    shown = client.get(f"/api/layers/{cloudy_stack}/display?bands=nir").json()

    assert shown["note"].startswith("Drawn at 1/2 of the data's resolution")
    assert _rgba(client, shown["url"], *_stac_point(20, 20))[3] == 255


def test_values_through_time_say_when_the_ground_was_hidden(
    client: TestClient, cloudy_stack: str
) -> None:
    lon, lat = _stac_point(5, 2)

    values = client.get(f"/api/layers/{cloudy_stack}/point", params={"lon": lon, "lat": lat})
    steps = values.json()["steps"]
    red = next(b for b in values.json()["bands"] if b["name"] == "red")

    assert [s["cloud_cover"] for s in steps] == list(syn.STAC_CLOUD)
    assert [s["obscured"] for s in steps] == [False, False, True]
    assert steps[2]["scene_class"] == "cloud, high probability"
    expected = [dn * syn.STAC_SCALE + syn.STAC_OFFSET for dn in (*syn.STAC_RED[:2], syn.CLOUD_DN)]
    assert red["values"] == pytest.approx(expected, abs=1e-9)
    assert red["values"][0] == 0  # rounded to the scale's precision, not -7e-09


def test_out_of_range_pixels_can_be_drawn_alone(
    client: TestClient, ws: Workspace, bad_ndvi_tif: Path, ndvi_tif: Path
) -> None:
    bad = load(ws, str(bad_ndvi_tif)).data_ref
    good = load(ws, str(ndvi_tif)).data_ref
    centre = _ndvi_centre(syn.NDVI_SIZE // 2 + 2, syn.NDVI_SIZE // 2 + 2)
    elsewhere = _ndvi_centre(5, 5)

    def url(ref: str) -> str:
        return f"/api/layers/{ref}/tiles/{{z}}/{{x}}/{{y}}.png?bands=ndvi&outside=-1,1"

    assert _rgba(client, url(bad), *centre) == (255, 92, 92, 255)
    assert _rgba(client, url(bad), *elsewhere)[3] == 0
    assert _rgba(client, url(good), *centre)[3] == 0


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

    page = TestClient(create_app(ws, dist=dist), base_url=LOCAL_URL).get("/")

    assert page.status_code == 200 and "<title>loci</title>" in page.text


def test_only_this_computers_own_pages_can_use_the_app(ws: Workspace) -> None:
    app = create_app(ws)
    local = TestClient(app, base_url=LOCAL_URL)
    rebound = TestClient(app, base_url="http://attacker.example:8765")

    foreign_host = rebound.get("/api/layers")
    cross_site = local.post(
        "/api/agent/decide",
        json={"id": "p-00000000", "accepted": True},
        headers={"Origin": "https://attacker.example"},
    )
    same_site = local.post(
        "/api/agent/decide",
        json={"id": "p-00000000", "accepted": True},
        headers={"Origin": LOCAL_URL},
    )

    assert foreign_host.status_code == 403  # a page that rebinds its name to this computer
    assert cross_site.status_code == 403  # a page elsewhere asking for a change
    assert same_site.status_code == 404  # let through; nothing waits under that id
    assert local.get("/api/layers").status_code == 200  # tools and tests send no Origin
    assert TestClient(app, base_url="http://localhost:5173").get("/api/layers").status_code == 200


def test_a_demo_project_offers_its_questions(ws: Workspace, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    write_demo(project)

    plain = TestClient(create_app(ws), base_url=LOCAL_URL).get("/api/project").json()
    demo = TestClient(create_app(ws, project_dir=project), base_url=LOCAL_URL)
    offered = demo.get("/api/project").json()["suggestions"]

    assert plain["suggestions"] == []
    assert offered[0].startswith("data/dem.tif is an elevation model")
    assert len(offered) == 5
