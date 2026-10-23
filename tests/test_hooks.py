"""The validation hook, fed PostToolUse events shaped like the ones Claude Code sends."""

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from loci import hooks
from loci.tools.load import load
from loci.tools.run import run
from loci.workspace import Workspace

ROOT = Path(__file__).parents[1]


def _event(tool: str, result: Any) -> dict[str, Any]:
    """Claude Code passes an MCP tool's result to the hook as its JSON text."""
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": f"mcp__loci__{tool}",
        "tool_input": {},
        "tool_response": json.dumps(result),
    }


def _loaded(ws: Workspace, path: Path) -> dict[str, Any]:
    return _event("load", load(ws, str(path)).model_dump(mode="json"))


def test_a_clean_load_is_reported_as_passing(ws: Workspace, ramp_tif: Path) -> None:
    output = hooks.respond(_loaded(ws, ramp_tif), ws, "report")

    assert output is not None and "systemMessage" not in output
    context = output["hookSpecificOutput"]["additionalContext"]
    assert re.fullmatch(r"loci validated raster-\w{8} automatically: pass \(.*\)\.", context)


def test_report_mode_passes_failures_on_and_tells_the_user(ws: Workspace, bare_tif: Path) -> None:
    output = hooks.respond(_loaded(ws, bare_tif), ws, "report")

    assert output is not None and "decision" not in output
    assert "failed validation" in output["systemMessage"]
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "automatically: fail" in context
    assert "- fail crs_defined:" in context and "Hint: Reload it with crs=" in context


def test_block_mode_returns_failures_as_a_problem_to_resolve(
    ws: Workspace, bare_tif: Path, ramp_tif: Path
) -> None:
    blocked = hooks.respond(_loaded(ws, bare_tif), ws, "block")
    passed = hooks.respond(_loaded(ws, ramp_tif), ws, "block")

    assert blocked is not None and blocked["decision"] == "block"
    assert "Resolve this before building on raster-" in blocked["reason"]
    assert passed is not None and "decision" not in passed  # only failures block


def test_off_and_unknown_modes(ws: Workspace, ramp_tif: Path) -> None:
    event = _loaded(ws, ramp_tif)

    assert hooks.respond(event, ws, "off") is None
    unknown = hooks.respond(event, ws, "loud")
    assert unknown is not None and "not one of" in unknown["systemMessage"]


def test_run_outputs_use_the_validation_run_already_did(
    ws: Workspace, ramp_tif: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dem = load(ws, str(ramp_tif)).data_ref
    code = "from loci import script\nscript.save(script.read('dem') * 2, 'doubled.tif')\n"
    result = run(ws, code=code, inputs={"dem": dem})

    def no_second_validation(*_: Any, **__: Any) -> None:
        raise AssertionError("validated twice")

    monkeypatch.setattr(hooks, "validate", no_second_validation)
    output = hooks.respond(_event("run", result.model_dump(mode="json")), ws, "report")

    assert output is not None
    context = output["hookSpecificOutput"]["additionalContext"]
    assert f"loci validated {result.outputs[0].data_ref} automatically: pass" in context


@pytest.mark.parametrize(
    ("response", "refs"),
    [
        ('{"data_ref": "raster-0a1b2c3d"}', ["raster-0a1b2c3d"]),
        ({"structuredContent": {"data_ref": "vector-0a1b2c3d"}}, ["vector-0a1b2c3d"]),
        ([{"type": "text", "text": '{"data_ref": "raster-0a1b2c3d"}'}], ["raster-0a1b2c3d"]),
        (
            {"outputs": [{"data_ref": "raster-0a1b2c3d"}, {"data_ref": None}]},
            ["raster-0a1b2c3d"],
        ),
        ('{"data_ref": "../escape"}', []),
        ("not json", []),
        (None, []),
    ],
)
def test_new_refs_are_found_in_every_response_shape(response: Any, refs: list[str]) -> None:
    assert hooks.new_refs(response) == refs


def test_main_reads_stdin_and_never_crashes_the_session(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOCI_WORKSPACE", str(ws.root))
    monkeypatch.delenv(hooks.MODE_ENV, raising=False)
    event = _event("load", {"data_ref": "raster-00000000"})  # not in this workspace
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))

    hooks.main()

    output = json.loads(capsys.readouterr().out)
    assert output["systemMessage"].startswith("loci validation hook failed: UnknownRefError")


def test_the_settings_hook_matches_exactly_the_tools_that_make_data_refs() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    matcher = settings["hooks"]["PostToolUse"][0]["matcher"]

    matched = [
        tool
        for tool in ("load", "stac_search", "run", "inspect", "validate", "preview", "lineage")
        if re.search(matcher, f"mcp__loci__{tool}")
    ]
    assert matched == ["load", "stac_search", "run"]
