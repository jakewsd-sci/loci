import json
import os
import shutil
from pathlib import Path

import pytest
import xarray as xr

from loci.errors import UnknownRefError
from loci.io import detect_format, read
from loci.models import ReadOptions
from loci.workspace import REF_PATTERN, Workspace


def _register(ws: Workspace, path: Path) -> str:
    uri = str(path)
    return ws.register(read(uri, detect_format(uri), ReadOptions()), uri).data_ref


def test_register_writes_a_readable_record(ws: Workspace, ramp_tif: Path) -> None:
    ref = _register(ws, ramp_tif)

    on_disk = json.loads((ws.refs_dir / f"{ref}.json").read_text())

    assert REF_PATTERN.fullmatch(ref)
    assert ref.startswith("raster-")
    assert on_disk["source"] == {
        "uri": str(ramp_tif),
        "format": "geotiff",
        "options": {"layer": None, "x_col": None, "y_col": None, "crs": None},
    }
    assert on_disk["session_id"] == ws.session_id
    assert on_disk["crs_source"] == "file"
    assert on_disk["fingerprint"]["size"] == ramp_tif.stat().st_size
    assert not list(ws.refs_dir.glob(".*.tmp"))


def test_another_workspace_on_the_same_root_reopens_refs(ws: Workspace, ramp_tif: Path) -> None:
    ref = _register(ws, ramp_tif)

    other = Workspace(ws.root)  # as a separate process would
    data = other.open(ref)

    assert other.session_id != ws.session_id
    assert isinstance(data, xr.Dataset)
    assert data.identical(ws.open(ref))


def test_reopening_reuses_resolved_options(ws: Workspace, points_lonlat_csv: Path) -> None:
    ref = _register(ws, points_lonlat_csv)

    record = Workspace(ws.root).record(ref)

    assert record.source.options == ReadOptions(x_col="lon", y_col="lat", crs="EPSG:4326")
    assert record.crs_source == "assumed"
    assert any("assumed EPSG:4326" in w for w in record.load_warnings)


@pytest.mark.parametrize(
    "bad_ref",
    [
        "raster-xyz",
        "raster-0123ABCD",
        "../../etc/passwd",
        "raster-0123abcd/../x",
        "",
        "table-0123abcd",
    ],
)
def test_malformed_refs_are_rejected(ws: Workspace, bad_ref: str) -> None:
    with pytest.raises(UnknownRefError, match="is not a data_ref"):
        ws.open(bad_ref)


def test_unknown_ref_hint_lists_recent_refs(ws: Workspace, ramp_tif: Path) -> None:
    ref = _register(ws, ramp_tif)

    with pytest.raises(UnknownRefError) as excinfo:
        ws.record("raster-00000000")

    assert excinfo.value.hint is not None
    assert ref in excinfo.value.hint


def test_unknown_ref_in_an_empty_workspace(ws: Workspace) -> None:
    with pytest.raises(UnknownRefError) as excinfo:
        ws.record("vector-00000000")

    assert excinfo.value.hint == "The workspace has no data_refs yet; call load first."


def test_records_are_listed_most_recent_first(
    ws: Workspace, ramp_tif: Path, dirty_gpkg: Path
) -> None:
    first = _register(ws, ramp_tif)
    second = _register(ws, dirty_gpkg)

    assert [r.data_ref for r in ws.records()] == [second, first]


def test_changed_source_is_detected(ws: Workspace, ramp_tif: Path, tmp_path: Path) -> None:
    copy = Path(shutil.copy(ramp_tif, tmp_path / "copy.tif"))
    record = ws.record(_register(ws, copy))
    assert not ws.source_changed(record)

    stat = copy.stat()
    os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert ws.source_changed(record)


def test_from_env_uses_the_configured_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCI_WORKSPACE", str(tmp_path / "elsewhere"))

    ws = Workspace.from_env()

    assert ws.root == (tmp_path / "elsewhere").resolve()
    assert ws.refs_dir.is_dir()
