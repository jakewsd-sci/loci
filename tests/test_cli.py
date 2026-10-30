"""The `loci` command: a demo project, a free port, and a check of the installation."""

import socket
from pathlib import Path

import pytest

from loci import cli
from loci.demo import FILES, MARKER, QUESTIONS
from loci.errors import LociError
from loci.tools.load import load
from loci.workspace import Workspace


def test_the_demo_project_holds_data_questions_and_answers(tmp_path: Path) -> None:
    folder = cli.make_demo(tmp_path / "demo")

    ws = Workspace(folder / ".loci")
    loaded = {name: load(ws, str(folder / name)) for name in FILES}
    readme = (folder / "README.md").read_text(encoding="utf-8")
    answers = (folder / "ANSWERS.md").read_text(encoding="utf-8")

    assert (folder / MARKER).exists()
    assert all(result.data_ref for result in loaded.values())
    assert loaded["data/sites.csv"].warnings  # the trap: positions without a stated CRS
    assert all(q.text in readme and q.answer in answers for q in QUESTIONS)
    assert "50 m" in answers  # east minus west, exactly


def test_the_demo_keeps_what_is_there_and_will_not_fill_another_folder(tmp_path: Path) -> None:
    folder = cli.make_demo(tmp_path / "demo")
    (folder / "data" / "dem.tif").write_bytes(b"changed")
    other = tmp_path / "thesis"
    other.mkdir()
    (other / "chapter1.docx").write_bytes(b"")

    cli.make_demo(folder)  # again, as `loci demo` does each time

    assert (folder / "data" / "dem.tif").read_bytes() == b"changed"
    with pytest.raises(LociError, match="already holds other files"):
        cli.make_demo(other)


def test_the_app_takes_the_first_free_port() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        busy = taken.getsockname()[1]
        assert cli._free_port(busy) != busy


def test_doctor_reports_the_app_gdal_claude_code_and_each_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(cli, "default_dist", lambda: tmp_path)

    healthy = cli.doctor()
    report = capsys.readouterr().out

    assert healthy
    assert "[ok] web app" in report and "GDAL" in report and "Claude Code" in report
    assert "[--] OpenAI: not set up" in report  # optional; the map works without it
    assert "Claude: set up" in report


@pytest.mark.parametrize(
    ("runtime", "system", "mode", "expected"),
    [
        ("/usr/bin/srt", "linux", "", "[ok] sandbox for scripts: srt at /usr/bin/srt"),
        (None, "win32", "", "[ok] sandbox for scripts: loci's guard"),  # all Windows has
        (None, "darwin", "", "[--] sandbox for scripts: loci's guard only"),
        ("/usr/bin/srt", "linux", "off", "[--] sandbox for scripts: off (LOCI_SANDBOX=off)"),
    ],
)
def test_doctor_says_how_scripts_are_sandboxed(
    runtime: str | None,
    system: str,
    mode: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "os_sandbox", lambda: runtime)
    monkeypatch.setattr(cli, "_claude_code", lambda: None)  # shutil.which goes by the system too
    monkeypatch.setattr(cli.sys, "platform", system)
    monkeypatch.setenv("LOCI_SANDBOX", mode)

    cli.doctor()

    assert expected in capsys.readouterr().out
