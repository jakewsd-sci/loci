"""Data in and out of the app: the project's files, files dropped on the map, and exports."""

import shutil
from pathlib import Path

import pytest
import rasterio
from starlette.testclient import TestClient

from loci import synthetic as syn
from loci.web.app import DEFAULT_PORT, create_app
from loci.workspace import Workspace

LOCAL_URL = f"http://127.0.0.1:{DEFAULT_PORT}"  # the app answers to its own names


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
def client(ws: Workspace, project: Path) -> TestClient:
    return TestClient(create_app(ws, project_dir=project), base_url=LOCAL_URL)


def test_the_projects_own_data_files_are_listed(
    client: TestClient, project: Path, ramp_tif: Path, polygons_gpkg: Path
) -> None:
    shutil.copy(ramp_tif, project / "ramp.tif")
    (project / "field" / "2024").mkdir(parents=True)
    shutil.copy(polygons_gpkg, project / "field" / "2024" / "plots.gpkg")
    for skipped in (".git/x.tif", "analysis/x.tif", "node_modules/x.tif", "notes/config.json"):
        (project / skipped).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ramp_tif, project / skipped)

    listed = client.get("/api/files").json()

    # A folder's own files come before those of its sub-folders.
    assert [(f["path"], f["kind"]) for f in listed["files"]] == [
        ("ramp.tif", "raster"),
        ("field/2024/plots.gpkg", "vector"),
    ]
    assert listed["more"] is False and all(f["data_ref"] is None for f in listed["files"])


def test_a_listed_file_goes_on_the_map_with_a_click(
    client: TestClient, project: Path, ramp_tif: Path
) -> None:
    shutil.copy(ramp_tif, project / "ramp.tif")

    loaded = client.post("/api/files/load", json={"path": "ramp.tif"})
    escaped = client.post("/api/files/load", json={"path": "../ramp.tif"})
    listed = client.get("/api/files").json()["files"]

    assert loaded.status_code == 200 and loaded.json()["kind"] == "raster"
    assert listed[0]["data_ref"] == loaded.json()["data_ref"]
    layer = client.get(f"/api/layers/{loaded.json()['data_ref']}").json()["layer"]
    assert layer["validation"] == "pass"  # checked at once, as the agent's loads are
    assert escaped.status_code == 400  # only files inside the project


def test_dropped_files_are_saved_in_data_and_never_replaced_unasked(
    client: TestClient, project: Path, ramp_tif: Path
) -> None:
    content = ramp_tif.read_bytes()

    saved = client.put("/api/files/data/ramp.tif", content=content)
    again = client.put("/api/files/data/ramp.tif", content=b"other")
    replaced = client.put("/api/files/data/ramp.tif?replace=1", content=content)
    refused = {
        name: client.put(f"/api/files/data/{name}", content=b"x").status_code
        for name in (".hidden.tif", "run.exe", "a%5Cb.tif", "..%2F..%2Fx.tif")
    }

    assert saved.json() == {"path": "data/ramp.tif", "loadable": True, "unchanged": False}
    assert replaced.json()["unchanged"] is True  # the same bytes again: left as it was
    assert again.status_code == 409 and "already in the project" in again.json()["error"]
    assert replaced.status_code == 200
    assert (project / "data" / "ramp.tif").read_bytes() == content
    # Hidden files, programs and Windows paths are refused; a / never reaches the handler.
    assert refused == {".hidden.tif": 400, "run.exe": 400, "a%5Cb.tif": 400, "..%2F..%2Fx.tif": 404}
    assert sorted(p.name for p in project.rglob("*") if p.is_file()) == ["ramp.tif"]
    assert not list((project / "data").glob(".*.part"))  # nothing half-written left behind


def test_a_dropped_shapefile_arrives_with_its_companions(
    client: TestClient, project: Path, tmp_path: Path
) -> None:
    shapefile = syn.write_polygons(tmp_path / "parcels.shp")
    for part in sorted(tmp_path.glob("parcels.*")):
        response = client.put(f"/api/files/data/{part.name}", content=part.read_bytes())
        assert response.status_code == 200, part.name

    loaded = client.post("/api/files/load", json={"path": "data/parcels.shp"})

    assert shapefile.exists() and loaded.status_code == 200
    assert loaded.json()["summary"]["feature_count"] == 4


def test_layers_are_exported_beside_the_data_and_downloaded(
    client: TestClient, project: Path, ramp_tif: Path
) -> None:
    shutil.copy(ramp_tif, project / "ramp.tif")
    ref = client.post("/api/files/load", json={"path": "ramp.tif"}).json()["data_ref"]

    first = client.post(f"/api/layers/{ref}/export", json={"format": "geotiff"}).json()
    second = client.post(f"/api/layers/{ref}/export", json={"format": "geotiff"}).json()
    download = client.get(f"/api/exports/{first['download']}")
    wrong = client.post(f"/api/layers/{ref}/export", json={"format": "geojson"})

    assert (first["download"], second["download"]) == ("ramp.tif", "ramp-2.tif")
    with rasterio.open(project / "exports" / "ramp.tif") as written:
        assert written.crs is not None and written.count == 1
    assert download.status_code == 200 and download.content.startswith(b"II*")  # a TIFF
    assert "attachment" in download.headers["content-disposition"]
    assert wrong.status_code == 400 and "cannot be exported" in wrong.json()["error"]
    assert client.get("/api/exports/..%2Fram.tif").status_code == 404


def test_a_zarr_store_is_listed_once_and_mapped(client: TestClient, project: Path) -> None:
    syn.write_climate_zarr(project / "era5.zarr")

    listed = client.get("/api/files").json()["files"]
    loaded = client.post("/api/files/load", json={"path": "era5.zarr"})

    assert [(f["path"], f["format"]) for f in listed] == [("era5.zarr", "zarr")]
    assert listed[0]["size"] > 0
    assert loaded.status_code == 200 and loaded.json()["summary"]["dims"]["time"] == 240
