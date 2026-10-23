"""End to end in Claude Code: a real session starts the server from .mcp.json, calls a tool, and
the project's hook validates the result. Each test is a short model session, so they cost money
and are deselected by default; run them with `uv run pytest -m claude`.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.claude,
    pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI"),
]

ROOT = Path(__file__).parents[1]
NOT_LOCI = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,Agent"


def _session(prompt: str, workspace: Path, mode: str) -> list[dict[str, Any]]:
    """Run `claude -p` in this project with only the loci tools; return its event stream."""
    settings = workspace.parent / "settings.json"
    settings.write_text(json.dumps({"env": {"LOCI_VALIDATION_HOOK": mode}}))
    command = [
        *("claude", "-p", prompt, "--settings", str(settings)),
        *("--allowedTools", "mcp__loci", "--disallowedTools", NOT_LOCI),
        *("--output-format", "stream-json", "--verbose"),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "LOCI_WORKSPACE": str(workspace)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _hook_output(events: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    name = f"PostToolUse:mcp__loci__{tool}"
    (event,) = [e for e in events if e.get("subtype") == "hook_response" and e["hook_name"] == name]
    output: dict[str, Any] = json.loads(event["stdout"])
    return output


def _calls(events: list[dict[str, Any]]) -> list[str]:
    return [
        block["name"]
        for e in events
        if e.get("type") == "assistant"
        for block in e["message"]["content"]
        if block["type"] == "tool_use"
    ]


@pytest.mark.parametrize("mode", ["report", "block"])
def test_a_load_in_claude_code_is_validated_by_the_hook(
    tmp_path: Path, bare_tif: Path, mode: str
) -> None:
    events = _session(
        f"Load {bare_tif} with the loci tools, then stop and tell me its data_ref.",
        tmp_path / ".loci",
        mode,
    )
    output = _hook_output(events, "load")
    final = events[-1]

    assert "mcp__loci__load" in _calls(events)
    assert output["systemMessage"].endswith("failed validation.")  # the file has no CRS
    if mode == "block":
        assert output["decision"] == "block" and "crs_defined" in output["reason"]
    else:
        assert "fail crs_defined" in output["hookSpecificOutput"]["additionalContext"]
    assert final["type"] == "result" and not final["is_error"]
    ref = re.search(r"raster-[0-9a-f]{8}", output["systemMessage"])
    assert ref is not None and ref.group() in final["result"]
