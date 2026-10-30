"""Scripts started by run stay in their run folder: loci's guard everywhere, and the operating
system's sandbox (srt) where it is installed."""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loci import sandbox, synthetic
from loci.errors import LociError
from loci.guard import PRIVATE_ENV, Guard
from loci.io import STAC_RASTER
from loci.io.stac import save_items
from loci.models import ReadOptions
from loci.tools.run import _input_uris, run
from loci.workspace import RefRecord, SourceSpec, Workspace


def _in_srt(folder: Path, code: str, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    """Run Python code in Anthropic's sandbox runtime, as run would, but without loci's guard."""
    runtime = sandbox.os_sandbox()
    assert runtime is not None
    settings = folder / sandbox.SETTINGS_FILE
    settings.write_text(
        json.dumps(sandbox.srt_settings(folder, sandbox.Reach(), env)), encoding="utf-8"
    )
    command = [runtime, "--settings", str(settings), "--", sys.executable, "-c", code]
    return subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)


def _srt_works() -> bool:
    """Whether Anthropic's sandbox runtime is installed and can run a program here."""
    if sandbox.os_sandbox() is None:
        return False
    with tempfile.TemporaryDirectory() as folder:
        return _in_srt(Path(folder), "pass", {}).returncode == 0


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard alone, whether or not srt is installed here."""
    monkeypatch.setenv(sandbox.MODE_ENV, "guard")


def test_the_guard_allows_the_run_folder_and_refuses_the_rest(tmp_path: Path) -> None:
    guard = Guard(str(tmp_path / "run"), [], [str(tmp_path / "keys.toml"), str(tmp_path / ".ssh")])

    guard.check("open", (str(tmp_path / "run" / "out.tif"), "w", -1))
    guard.check("open", (str(tmp_path / "elsewhere.csv"), "r", -1))  # reading is fine
    guard.check("open", (os.devnull, "w", -1))
    guard.check("open", (7, "w", -1))  # an open file descriptor
    with pytest.raises(PermissionError, match="loci's guard stopped writing"):
        guard.check("open", (str(tmp_path / "elsewhere.csv"), "a", -1))
    with pytest.raises(PermissionError, match="loci's guard stopped reading"):
        guard.check("open", (str(tmp_path / "keys.toml"), "r", -1))
    with pytest.raises(PermissionError, match="reading"):
        guard.check("open", (str(tmp_path / ".ssh" / "id_ed25519"), "rb", -1))
    with pytest.raises(PermissionError, match="changing"):
        guard.check("os.rename", (str(tmp_path / "run" / "a"), str(tmp_path / "b"), None, None))
    with pytest.raises(PermissionError, match="starting a program"):
        guard.check("subprocess.Popen", ("ls", ["ls"], None, None))
    with pytest.raises(PermissionError, match=r"reaching example\.com"):
        guard.check("socket.getaddrinfo", ("example.com", 443, 0, 0, 0))


def test_the_guard_reaches_only_the_inputs_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.lab:3128")
    guard = Guard(str(tmp_path), ["data.example.org"])
    resolve = guard.resolver(lambda host, *a: [(2, 1, 6, "", ("192.0.2.7", 443))])

    resolve("data.example.org", 443)
    guard.check("socket.getaddrinfo", ("data.example.org", 443, 0, 0, 0))
    guard.check("socket.getaddrinfo", ("proxy.lab", 3128, 0, 0, 0))  # the way to the inputs
    guard.check("socket.connect", (None, ("192.0.2.7", 443)))
    with pytest.raises(PermissionError, match=r"connecting to 192\.0\.2\.99"):
        guard.check("socket.connect", (None, ("192.0.2.99", 443)))
    without_inputs = Guard(str(tmp_path), [])
    with pytest.raises(PermissionError, match=r"reaching proxy\.lab \(the network's proxy\)"):
        without_inputs.check("socket.getaddrinfo", ("proxy.lab", 3128, 0, 0, 0))
    # What requests and urllib ask of the proxy is seen too.
    guard.check("http.client.send", (None, b"CONNECT data.example.org:443 HTTP/1.1\r\n\r\n"))
    guard.check("http.client.send", (None, b"GET /tiles/1.tif HTTP/1.1\r\n"))  # through a tunnel
    with pytest.raises(PermissionError, match=r"reaching elsewhere\.example through the network"):
        guard.check("http.client.send", (None, b"CONNECT elsewhere.example:443 HTTP/1.1\r\n"))
    with pytest.raises(PermissionError, match="through the network's proxy"):
        guard.check("http.client.send", (None, b"GET http://elsewhere.example/a HTTP/1.1\r\n"))


@pytest.mark.usefixtures("guarded")
def test_a_script_cannot_write_or_delete_outside_its_run_folder(
    ws: Workspace, tmp_path: Path
) -> None:
    precious = tmp_path / "field-notes.csv"
    precious.write_text("site,value\n", encoding="utf-8")

    written = run(ws, code=f"open(r'{tmp_path / 'stray.txt'}', 'w').write('x')\n")
    deleted = run(ws, code=f"import os\nos.remove(r'{precious}')\n")

    assert (written.status, written.sandbox) == ("error", "guard")
    assert any(w.startswith("loci's guard stopped writing") for w in written.warnings)
    assert not (tmp_path / "stray.txt").exists()
    assert deleted.status == "error" and precious.exists()


@pytest.mark.usefixtures("guarded")
def test_a_script_cannot_read_loci_s_keys(
    ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "keys.toml").write_text('[openai]\napi_key = "sk-secret"\n', encoding="utf-8")
    monkeypatch.setenv("LOCI_CONFIG", str(settings / "config.toml"))

    result = run(ws, code=f"print(open(r'{settings / 'keys.toml'}').read())\n")

    assert result.status == "error" and "sk-secret" not in result.stdout
    assert any(w.startswith("loci's guard stopped reading") for w in result.warnings)


@pytest.mark.usefixtures("guarded")
def test_a_script_cannot_start_programs_or_reach_the_network(ws: Workspace) -> None:
    program = run(ws, code="import subprocess, sys\nsubprocess.run([sys.executable, '-V'])\n")
    network = run(ws, code="import socket\nsocket.create_connection(('example.com', 443), 5)\n")

    assert program.status == "error"
    assert any("starting a program" in w for w in program.warnings)
    assert network.status == "error"
    assert any("reaching example.com" in w for w in network.warnings)


@pytest.mark.usefixtures("guarded")
def test_what_a_script_needs_still_works(ws: Workspace, ramp_tif: Path) -> None:
    from loci.tools.load import load

    dem = load(ws, str(ramp_tif)).data_ref
    code = """
import tempfile
import matplotlib.pyplot as plt
from loci import script

with tempfile.NamedTemporaryFile("w", suffix=".txt") as scratch:
    scratch.write("intermediate")
plt.plot([1, 2, 3])
plt.savefig(script.output_path("plot.png"))
script.save(script.read("dem") * 2, "doubled.tif")
"""
    result = run(ws, code=code, inputs={"dem": dem})

    assert result.status == "ok", result.stderr
    assert sorted(o.file for o in result.outputs) == ["doubled.tif", "plot.png"]
    assert result.notes[-1].startswith("Ran with loci's guard")
    fonts = list((ws.root / "runs" / result.run_id / ".tmp" / "matplotlib").glob("fontlist-*"))
    assert len(fonts) == 1  # matplotlib's font list, given to the run rather than rebuilt


def test_the_sandbox_can_be_turned_off(
    ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sandbox.MODE_ENV, "off")

    result = run(ws, code=f"open(r'{tmp_path / 'allowed.txt'}', 'w').write('x')\n")

    assert (result.status, result.sandbox) == ("ok", "off")
    assert (tmp_path / "allowed.txt").exists()
    assert "without a sandbox" in result.notes[-1]


def test_srt_wraps_the_guard_where_it_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "os_sandbox", lambda: "/usr/local/bin/srt")
    elsewhere = {"LOCI_CONFIG": str(tmp_path / "mine" / "config.toml")}

    planned = sandbox.plan("script.py", tmp_path, ["https://cogs.example/b04.tif"], env=elsewhere)
    settings = json.loads((tmp_path / sandbox.SETTINGS_FILE).read_text(encoding="utf-8"))
    guard_only = sandbox.plan("script.py", tmp_path, [], env={sandbox.MODE_ENV: "guard"})

    assert planned.mode == "os"
    srt = ["/usr/local/bin/srt", "--settings", str(tmp_path / sandbox.SETTINGS_FILE), "--"]
    assert planned.command == [*srt, sys.executable, "-m", "loci.guard", "script.py"]
    assert settings["network"] == {"allowedDomains": ["cogs.example"], "deniedDomains": []}
    assert settings["filesystem"]["allowWrite"] == [str(tmp_path)]
    hidden = settings["filesystem"]["denyRead"]
    assert "~/.config/loci" in hidden and str(tmp_path / "mine" / "keys.toml") in hidden
    assert "~/.aws" in hidden and "~/.netrc" not in hidden  # curl may need it for the input
    assert planned.env[PRIVATE_ENV].split(os.pathsep) == hidden  # the guard hides the same
    assert guard_only.mode == "guard"
    intranet = sandbox.plan("script.py", tmp_path, ["http://fileserver/dem.tif"], env={})
    assert intranet.mode == "guard" and "cannot allow fileserver" in intranet.note
    with pytest.raises(LociError, match="not one of auto, guard or off"):
        sandbox.plan("script.py", tmp_path, [], env={sandbox.MODE_ENV: "loose"})


def test_scripts_reach_what_their_inputs_need() -> None:
    needs = sandbox.reach(
        [
            "https://storage.googleapis.com/era5.zarr",
            "/vsizip//vsicurl/https://Data.example.org/rivers.zip/rivers.shp",
            "s3://sentinel-cogs/tiles/b04.tif",
            "/home/ana/dem.tif",
            r"C:\Users\ana\dem.tif",
        ],
        {"AWS_REGION": "us-west-2"},
    )

    assert needs.hosts == [
        "data.example.org",
        "sentinel-cogs.s3.amazonaws.com",
        "sentinel-cogs.s3.us-west-2.amazonaws.com",
        "storage.googleapis.com",
    ]
    assert needs.credentials == ["~/.aws", "~/.netrc", "~/_netrc"]
    assert sandbox.reach(["/home/ana/dem.tif"], {}) == sandbox.Reach()


def test_a_stac_input_reaches_the_assets_it_loaded(tmp_path: Path) -> None:
    items = synthetic.stac_items(tmp_path / "scenes")
    for item in items:
        for band, asset in item.assets.items():
            asset.href = f"https://{band}.cogs.example/{item.id}.tif"
    save_items(items, tmp_path / "items.json", {})
    record = RefRecord(
        data_ref="raster-00000000",
        kind="raster",
        source=SourceSpec(
            uri=str(tmp_path / "items.json"),
            format=STAC_RASTER.name,
            options=ReadOptions(bands=["red", "nir"]),
        ),
        created_at=datetime.now(UTC),
        session_id="s",
    )

    hosts = sandbox.reach(_input_uris([record]), {}).hosts

    assert hosts == ["nir.cogs.example", "red.cogs.example"]  # not scl, which was not loaded


# CI's sandbox job sets LOCI_TEST_SRT=required, so a broken srt fails these instead of skipping.
srt_only = pytest.mark.skipif(
    os.environ.get("LOCI_TEST_SRT") != "required" and not _srt_works(),
    reason="Anthropic's sandbox runtime cannot run here",
)


@srt_only
def test_a_script_runs_inside_srt_where_it_works(
    ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sandbox.MODE_ENV, "auto")

    ok = run(ws, code="from loci import script\nscript.save({'answer': 42}, 'answer.json')\n")
    stray = run(ws, code=f"open(r'{tmp_path / 'stray.txt'}', 'w').write('x')\n")

    assert (ok.status, ok.sandbox) == ("ok", "os"), ok.stderr
    assert [o.file for o in ok.outputs] == ["answer.json"]
    assert stray.status == "error" and not (tmp_path / "stray.txt").exists()


@srt_only
def test_srt_holds_even_code_the_guard_cannot_see(tmp_path: Path) -> None:
    """C libraries, such as GDAL, pass the guard by; srt stops them all the same."""
    run_dir, settings = tmp_path / "run", tmp_path / "settings"
    run_dir.mkdir()
    settings.mkdir()
    (settings / "keys.toml").write_text('api_key = "sk-secret"\n', encoding="utf-8")
    env = {"LOCI_CONFIG": str(settings / "config.toml")}

    write = _in_srt(run_dir, f"open(r'{tmp_path / 'stray.txt'}', 'w')", env)
    read = _in_srt(run_dir, f"print(open(r'{settings / 'keys.toml'}').read())", env)
    inside = _in_srt(run_dir, f"open(r'{run_dir / 'ok.txt'}', 'w').write('x')", env)
    away = _in_srt(
        run_dir, "import urllib.request as u; u.urlopen('https://example.com', None, 20)", env
    )

    assert write.returncode != 0 and not (tmp_path / "stray.txt").exists()
    assert read.returncode != 0 and "sk-secret" not in read.stdout
    assert inside.returncode == 0 and (run_dir / "ok.txt").exists()
    assert away.returncode != 0  # no input is there
