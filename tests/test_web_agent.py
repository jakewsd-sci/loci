"""Scripts, runs, live events and the agent bridge in the web API.

The agent tests replace the Claude Agent SDK's query with a scripted one, so they need no model:
they check how messages, tool calls and the validation hook become events for the app.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from starlette.testclient import TestClient

from loci.tools.load import load
from loci.web.agent import Agent
from loci.web.app import create_app, workspace_fingerprint
from loci.workspace import Workspace

DOUBLE = "from loci import script\nscript.save(script.read('dem') * 2, 'doubled.tif')\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _client(ws: Workspace, project: Path, agent: Agent | None = None) -> TestClient:
    return TestClient(create_app(ws, project_dir=project, agent=agent))


def _sse(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_scripts_are_listed_read_and_saved_under_analysis(ws: Workspace, project: Path) -> None:
    client = _client(ws, project)

    assert client.get("/api/scripts").json() == []
    saved = client.put("/api/scripts/analysis/double.py", json={"content": DOUBLE})
    listed = client.get("/api/scripts").json()

    assert saved.status_code == 200 and (project / "analysis" / "double.py").read_text() == DOUBLE
    assert [s["path"] for s in listed] == ["analysis/double.py"]
    assert client.get("/api/scripts/analysis/double.py").json()["content"] == DOUBLE
    for bad in ("analysis/../secrets.py", "notes.py", "analysis/data.txt"):
        assert client.get(f"/api/scripts/{bad}").status_code == 400


def test_project_scripts_run_with_provenance_naming_the_file(
    ws: Workspace, project: Path, ramp_tif: Path
) -> None:
    client = _client(ws, project)
    client.put("/api/scripts/analysis/double.py", json={"content": DOUBLE})
    dem = load(ws, str(ramp_tif)).data_ref

    result = client.post(
        "/api/run", json={"path": "analysis/double.py", "inputs": {"dem": dem}}
    ).json()
    ref = result["outputs"][0]["data_ref"]
    detail = client.get(f"/api/layers/{ref}").json()

    assert result["status"] == "ok" and result["outputs"][0]["validation"] == "pass"
    arguments = detail["lineage"]["provenance"]["call"]["arguments"]
    assert arguments["path"] == str(project / "analysis" / "double.py")


def test_events_announce_workspace_changes(ws: Workspace, project: Path, ramp_tif: Path) -> None:
    client = _client(ws, project)
    before = workspace_fingerprint(ws)

    load(ws, str(ramp_tif))
    stream = client.get("/api/events?once=1")

    assert stream.text.startswith("event: layers\n")
    assert workspace_fingerprint(ws) != before
    assert workspace_fingerprint(ws) in stream.text


class ScriptedQuery:
    """Stands in for claude_agent_sdk.query: replays messages and calls the hook like the CLI."""

    def __init__(self, messages: list[Any], hook_input: dict[str, Any] | None = None) -> None:
        self.messages = messages
        self.hook_input = hook_input
        self.options: ClaudeAgentOptions | None = None
        self.prompt: str | None = None

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        self.prompt, self.options = prompt, options
        for message in self.messages:
            if self.hook_input is not None and isinstance(message, UserMessage):
                # The CLI runs PostToolUse hooks before it reports the tool's result.
                assert options.hooks is not None
                (matcher,) = options.hooks["PostToolUse"]
                await matcher.hooks[0](self.hook_input, "t1", {"signal": None})  # type: ignore[arg-type]
            yield message


def _conversation(result_text: str) -> list[Any]:
    return [
        SystemMessage(subtype="init", data={"session_id": "s-1"}),
        AssistantMessage(
            content=[
                TextBlock(text="Loading the DEM."),
                ToolUseBlock(id="t1", name="mcp__loci__load", input={"uri": "dem.tif"}),
            ],
            model="opus",
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=result_text)]),
        AssistantMessage(content=[TextBlock(text="Done.")], model="opus"),
        ResultMessage(
            subtype="success",
            duration_ms=2500,
            duration_api_ms=2000,
            is_error=False,
            num_turns=2,
            session_id="s-1",
            total_cost_usd=0.02,
        ),
    ]


def test_agent_messages_stream_as_events_with_automatic_checks(
    ws: Workspace, project: Path, bare_tif: Path
) -> None:
    loaded = load(ws, str(bare_tif))
    result_text = loaded.model_dump_json()
    hook_input = {
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__loci__load",
        "tool_input": {"uri": str(bare_tif)},
        "tool_response": result_text,
    }
    scripted = ScriptedQuery(_conversation(result_text), hook_input)
    client = _client(ws, project, Agent(ws, run_query=scripted))

    events = _sse(client.post("/api/agent", json={"message": "Load the DEM"}).text)

    kinds = [e["type"] for e in events]
    assert kinds == ["session", "text", "tool", "tool_result", "check", "text", "done"]
    assert events[2] == {"type": "tool", "id": "t1", "name": "load", "input": {"uri": "dem.tif"}}
    check = events[4]
    assert (check["data_ref"], check["status"]) == (loaded.data_ref, "fail")  # no CRS
    assert events[-1]["session_id"] == "s-1" and events[-1]["cost_usd"] == 0.02
    assert scripted.prompt == "Load the DEM"


def test_agent_options_allow_only_the_loci_tools_on_this_workspace(
    ws: Workspace, project: Path
) -> None:
    options = Agent(ws).options(model="sonnet", session_id="s-9")

    assert options.tools == [] and options.allowed_tools == ["mcp__loci"]
    assert options.strict_mcp_config and options.setting_sources == []
    assert isinstance(options.mcp_servers, dict)
    server = options.mcp_servers["loci"]
    assert server["env"] == {"LOCI_WORKSPACE": str(ws.root)}  # type: ignore[typeddict-item]
    assert (options.model, options.resume) == ("sonnet", "s-9")


def test_agent_failures_arrive_as_events(ws: Workspace, project: Path) -> None:
    async def broken(**_: Any) -> AsyncIterator[Any]:
        raise RuntimeError("no credentials")
        yield  # pragma: no cover

    client = _client(ws, project, Agent(ws, run_query=broken))

    failed = _sse(client.post("/api/agent", json={"message": "hi"}).text)
    unknown = _sse(client.post("/api/agent", json={"message": "hi", "model": "gpt"}).text)
    empty = client.post("/api/agent", json={"message": " "})

    assert failed == [{"type": "error", "message": "RuntimeError: no credentials"}]
    assert unknown[0]["message"] == "Unknown model 'gpt'."
    assert empty.status_code == 400


def test_models_list_the_choices_for_the_picker(ws: Workspace, project: Path) -> None:
    models = _client(ws, project).get("/api/models").json()

    assert [m["id"] for m in models["models"]] == ["opus", "sonnet", "haiku"]
    assert models["default"] == "opus"
