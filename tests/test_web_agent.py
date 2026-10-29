"""Scripts, runs, live events and the agent bridge in the web API.

The agent tests replace the Claude Agent SDK's query with a scripted one, so they need no model:
they check how messages, tool calls and the validation hook become events for the app.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from starlette.testclient import TestClient

from loci.tools.load import load
from loci.web.agent import Agent
from loci.web.app import create_app, workspace_fingerprint
from loci.web.project import scripts_fingerprint
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


def test_results_link_to_their_script_and_the_exact_version_that_ran(
    ws: Workspace, project: Path, ramp_tif: Path
) -> None:
    client = _client(ws, project)
    client.put("/api/scripts/analysis/double.py", json={"content": DOUBLE})
    dem = load(ws, str(ramp_tif)).data_ref
    result = client.post("/api/run", json={"path": "analysis/double.py", "inputs": {"dem": dem}})
    ref, run_id = result.json()["outputs"][0]["data_ref"], result.json()["run_id"]

    before = client.get(f"/api/layers/{ref}").json()["script"]
    client.put("/api/scripts/analysis/double.py", json={"content": DOUBLE + "# changed\n"})
    after = client.get(f"/api/layers/{ref}").json()["script"]
    snapshot = client.get(f"/api/runs/{run_id}/script").json()

    assert before == {"run_id": run_id, "path": "analysis/double.py", "changed": False}
    assert after["changed"] is True
    assert snapshot["content"] == DOUBLE and snapshot["run_id"] == run_id
    assert client.get(f"/api/layers/{dem}").json()["script"] is None  # not made by run
    for bad in ("run-zz", "..%2Fsecrets", "run-00000000"):
        assert client.get(f"/api/runs/{bad}/script").status_code in (400, 404)


def test_events_announce_workspace_changes(ws: Workspace, project: Path, ramp_tif: Path) -> None:
    client = _client(ws, project)
    before = workspace_fingerprint(ws)

    load(ws, str(ramp_tif))
    first = client.get("/api/events?once=1").text
    client.put("/api/scripts/analysis/double.py", json={"content": DOUBLE})
    second = client.get("/api/events?once=1").text

    assert first.startswith("event: layers\n") and "event: scripts\n" in first
    assert workspace_fingerprint(ws) != before
    assert workspace_fingerprint(ws) in first
    assert scripts_fingerprint(project) in second and scripts_fingerprint(project) not in first


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


def test_agent_options_give_the_loci_tools_and_the_project_scripts(
    ws: Workspace, project: Path
) -> None:
    options = Agent(ws, project=project).options(model="sonnet", session_id="s-9")

    # Read, Write and Edit for scripts; nothing allowed up front, so every call is checked.
    assert options.tools == ["Read", "Write", "Edit"] and options.allowed_tools == []
    assert options.can_use_tool is not None and options.cwd == str(project.resolve())
    assert options.strict_mcp_config and options.setting_sources == []
    assert isinstance(options.mcp_servers, dict)
    server = options.mcp_servers["loci"]
    assert server["env"] == {"LOCI_WORKSPACE": str(ws.root)}  # type: ignore[typeddict-item]
    assert (options.model, options.resume) == ("sonnet", "s-9")
    assert isinstance(options.system_prompt, str)
    assert str(project.resolve() / "analysis") in options.system_prompt
    assert (project / "analysis").is_dir()


def test_file_tools_reach_only_scripts_under_analysis(ws: Workspace, project: Path) -> None:
    options = Agent(ws, project=project).options(model="sonnet", session_id=None)
    assert options.hooks is not None
    (guard,) = options.hooks["PreToolUse"][0].hooks

    def decision(path: str) -> str | None:
        hook_input: Any = {"tool_input": {"file_path": path}}
        output: dict[str, Any] = asyncio.run(guard(hook_input, "t", {"signal": None}))  # type: ignore[arg-type]
        return output.get("hookSpecificOutput", {}).get("permissionDecision")

    assert decision(str(project / "analysis" / "ndvi.py")) is None  # goes ahead
    assert decision("analysis/sub/ndvi.py") is None  # relative to the project
    for outside in (
        str(project / "data.csv"),
        str(project / "analysis" / "notes.txt"),
        str(project / "analysis" / ".." / "secret.py"),
        str(ws.root / "refs" / "raster-00000000.json"),
    ):
        assert decision(outside) == "deny", outside


class Permissions:
    """Stands in for query: asks the agent's permission callback about each call, like the CLI."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls
        self.answers: list[PermissionResultAllow | PermissionResultDeny] = []

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        yield SystemMessage(subtype="init", data={"session_id": "s-2"})
        assert options.can_use_tool is not None
        for i, (name, arguments) in enumerate(self.calls):
            context = ToolPermissionContext(tool_use_id=f"t{i}")
            self.answers.append(await options.can_use_tool(name, arguments, context))
        yield ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s-2",
            total_cost_usd=0.0,
        )


def _answer(
    agent: Agent,
    *,
    review: str = "changes",
    reply: Callable[[dict[str, Any]], tuple[bool, str]] = lambda _: (True, ""),
) -> list[dict[str, Any]]:
    """Every event of one message, answering each review as `reply` says."""

    async def main() -> list[dict[str, Any]]:
        events = []
        async for event in agent.send("Go", review=review):
            events.append(event)
            if event["type"] == "proposal":
                assert agent.decide(event["id"], *reply(event))
        return events

    return asyncio.run(main())


SCRIPT = "from loci import script\nprint(script.read('stack'))\n"


def test_new_scripts_are_written_freely_and_changes_wait_for_review(
    ws: Workspace, project: Path
) -> None:
    (project / "analysis").mkdir()
    existing = project / "analysis" / "ndvi.py"
    existing.write_text(SCRIPT)
    calls = Permissions(
        [
            ("Write", {"file_path": str(project / "analysis" / "new.py"), "content": SCRIPT}),
            ("Read", {"file_path": str(existing)}),
            ("Write", {"file_path": str(project / "results.csv"), "content": "x"}),
            ("Edit", {"file_path": str(existing), "old_string": "print", "new_string": "repr"}),
            ("Bash", {"command": "ls"}),
            ("mcp__loci__inspect", {"data_ref": "raster-00000000"}),
        ]
    )

    events = _answer(Agent(ws, project=project, run_query=calls))

    write, read, outside, edit, bash, inspect = calls.answers
    assert isinstance(write, PermissionResultAllow) and isinstance(read, PermissionResultAllow)
    assert isinstance(outside, PermissionResultDeny) and "analysis" in outside.message
    assert isinstance(edit, PermissionResultAllow)  # accepted below
    assert isinstance(bash, PermissionResultDeny) and isinstance(inspect, PermissionResultAllow)
    (proposal,) = [e for e in events if e["type"] == "proposal"]
    assert proposal["kind"] == "edit" and proposal["path"] == "analysis/ndvi.py"
    assert proposal["tool_use_id"] == "t3"
    assert (proposal["before"], proposal["after"]) == (SCRIPT, SCRIPT.replace("print", "repr"))
    decisions = [e for e in events if e["type"] == "decision"]
    assert decisions == [{"type": "decision", "id": proposal["id"], "accepted": True}]


def test_a_rejected_change_tells_the_agent_why(ws: Workspace, project: Path) -> None:
    (project / "analysis").mkdir()
    existing = project / "analysis" / "ndvi.py"
    existing.write_text(SCRIPT)
    calls = Permissions([("Write", {"file_path": str(existing), "content": "print(1)\n"})])

    _answer(Agent(ws, project=project, run_query=calls), reply=lambda _: (False, "keep water"))

    (answer,) = calls.answers
    assert isinstance(answer, PermissionResultDeny)
    assert answer.message == (
        "The scientist rejected this change to analysis/ndvi.py; nothing was changed. "
        "Their note: keep water"
    )


def test_runs_take_scripts_by_path_and_can_wait_for_review(ws: Workspace, project: Path) -> None:
    (project / "analysis").mkdir()
    (project / "analysis" / "ndvi.py").write_text(SCRIPT)
    inline = ("mcp__loci__run", {"code": SCRIPT, "inputs": {}})
    by_path = ("mcp__loci__run", {"path": "analysis/ndvi.py", "inputs": {"stack": "raster-1"}})
    missing = ("mcp__loci__run", {"path": "analysis/gone.py"})

    freely = Permissions([inline, by_path, missing])
    _answer(Agent(ws, project=project, run_query=freely))
    reviewed = Permissions([by_path])
    events = _answer(
        Agent(ws, project=project, run_query=reviewed), review="runs", reply=lambda _: (False, "")
    )

    code, allowed, gone = freely.answers
    assert isinstance(code, PermissionResultDeny) and "path=" in code.message
    assert isinstance(allowed, PermissionResultAllow)
    assert allowed.updated_input is not None  # made absolute, whatever the call gave
    assert allowed.updated_input["path"] == str(project.resolve() / "analysis" / "ndvi.py")
    assert isinstance(gone, PermissionResultDeny)
    (proposal,) = [e for e in events if e["type"] == "proposal"]
    assert (proposal["kind"], proposal["code"]) == ("run", SCRIPT)
    assert proposal["inputs"] == {"stack": "raster-1"}
    (answer,) = reviewed.answers
    assert isinstance(answer, PermissionResultDeny) and "run of analysis/ndvi.py" in answer.message


def test_an_abandoned_review_stops_waiting(ws: Workspace, project: Path) -> None:
    (project / "analysis").mkdir()
    existing = project / "analysis" / "ndvi.py"
    existing.write_text(SCRIPT)
    agent = Agent(
        ws,
        project=project,
        run_query=Permissions([("Write", {"file_path": str(existing), "content": "x\n"})]),
    )

    async def leave_after_the_proposal() -> None:
        events = agent.send("Go")
        async for event in events:
            if event["type"] == "proposal":
                break
        await events.aclose()  # the browser went away

    asyncio.run(leave_after_the_proposal())

    assert agent._pending == {}
    assert existing.read_text() == SCRIPT


def test_answers_reach_only_reviews_that_are_waiting(ws: Workspace, project: Path) -> None:
    client = _client(ws, project)

    unknown = client.post("/api/agent/decide", json={"id": "p-00000000", "accepted": True})
    malformed = client.post("/api/agent/decide", json={"id": "p-00000000", "accepted": "yes"})

    assert unknown.status_code == 404 and malformed.status_code == 400


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
