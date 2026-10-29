"""loci's own agent loop, for models of other providers (OpenAI, Gemini, OpenRouter).

A scripted Pydantic AI FunctionModel stands in for the provider and the loci MCP server runs in
process, so these tests need no keys or network: they check that the loop gives the model the
same tools, reviews and checks as Claude in Claude Code, and the app the same events.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic_ai.exceptions import ModelAuthenticationError, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from loci import provenance
from loci.errors import LociError
from loci.server import build_server
from loci.tools.load import load
from loci.web.agent import Agent
from loci.web.loop import build_model, read_history
from loci.web.providers import Settings, resolve
from loci.workspace import Workspace

SCRIPT = "from loci import script\nprint(script.read('stack'))\n"
DOUBLE = "from loci import script\nscript.save(script.read('dem') * 2, 'doubled.tif')\n"
KEYS = {"OPENAI_API_KEY": "sk-test", "GEMINI_API_KEY": "g-test"}


class Scripted:
    """Stands in for a provider's model: answers each request with the next scripted parts."""

    def __init__(self, *responses: list[ModelResponsePart]) -> None:
        self.responses = list(responses)
        self.requests: list[list[ModelMessage]] = []
        self.instructions: str | None = None

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(list(messages))
        self.instructions = info.instructions
        return ModelResponse(parts=self.responses.pop(0))

    def returns(self, request: int = -1) -> dict[str, str]:
        """What the tools returned to the model in a request, by tool call id."""
        message = self.requests[request][-1]
        assert isinstance(message, ModelRequest)
        return {
            p.tool_call_id: p.model_response_str()
            for p in message.parts
            if isinstance(p, ToolReturnPart)
        }


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    (folder / "analysis").mkdir(parents=True)
    return folder


def _agent(ws: Workspace, project: Path, model: Scripted, **env: str) -> Agent:
    return Agent(
        ws,
        project=project,
        loop_model=lambda choice: FunctionModel(model),
        loop_server=build_server,  # in process; the app starts it as a subprocess
        settings=lambda: Settings(env=env or KEYS),
    )


def _answer(
    agent: Agent,
    message: str = "Go",
    *,
    model: str = "openai:gpt-6-astra",
    session_id: str | None = None,
    review: str = "changes",
    reply: Callable[[dict[str, Any]], tuple[bool, str]] = lambda _: (True, ""),
) -> list[dict[str, Any]]:
    """Every event of one message, answering each review as `reply` says."""

    async def main() -> list[dict[str, Any]]:
        events = []
        async for event in agent.send(message, model=model, session_id=session_id, review=review):
            events.append(event)
            if event["type"] == "proposal":
                assert agent.decide(event["id"], *reply(event))
        return events

    return asyncio.run(main())


def _call(name: str, call_id: str, **arguments: Any) -> ToolCallPart:
    return ToolCallPart(name, arguments, tool_call_id=call_id)


def test_other_providers_answer_with_the_loci_tools_and_automatic_checks(
    ws: Workspace, project: Path, bare_tif: Path
) -> None:
    model = Scripted(
        [TextPart("Loading the DEM."), _call("load", "c1", uri=str(bare_tif))],
        [TextPart("Done.")],
    )

    events = _answer(_agent(ws, project, model), "Load the DEM")

    kinds = [e["type"] for e in events]
    assert kinds == ["session", "text", "tool", "tool_result", "check", "text", "done"]
    (record,) = ws.records()
    assert events[2] == {
        "type": "tool",
        "id": "c1",
        "name": "load",
        "input": {"uri": str(bare_tif)},
    }
    assert (events[4]["data_ref"], events[4]["status"]) == (record.data_ref, "fail")  # no CRS
    done = events[-1]
    assert done["session_id"] == events[0]["session_id"] and done["session_id"].startswith("loop-")
    assert done["cost_usd"] is None and done["tokens"]["output"] > 0  # a scripted model is free
    # The model reads the tool's result with the validation report, as Claude does.
    told = model.returns()["c1"]
    assert record.data_ref in told and f"loci validated {record.data_ref} automatically" in told
    assert model.instructions is not None
    assert str(project.resolve() / "analysis") in model.instructions  # loci's instructions
    assert "Data stays on disk" in model.instructions  # and the MCP server's


def test_a_conversation_continues_from_its_saved_history(ws: Workspace, project: Path) -> None:
    model = Scripted([TextPart("NDVI is (nir - red) / (nir + red).")], [TextPart("Yes.")])
    agent = _agent(ws, project, model)

    first = _answer(agent, "What is NDVI?")
    session = first[0]["session_id"]
    second = _answer(agent, "Is it unitless?", model="google:gemini-3.8-flash", session_id=session)

    assert second[0] == {"type": "session", "session_id": session}
    asked = [
        p.content
        for m in model.requests[1]
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    assert asked == ["What is NDVI?", "Is it unitless?"]  # another provider carries it on
    assert len(read_history(ws, session)) == 4


def test_conversations_stay_with_the_engine_that_began_them(ws: Workspace, project: Path) -> None:
    agent = _agent(ws, project, Scripted())

    claude_session = _answer(agent, session_id="0b5d2c1e-8f43-4a8e-9b1a-2f6c3d4e5a6b")
    loop_session = _answer(agent, model="claude:opus", session_id="loop-0123456789abcdef")
    missing = _answer(agent, session_id="loop-0123456789abcdef")

    assert claude_session[0]["message"].startswith("This conversation was held with Claude")
    assert "another provider's model" in loop_session[0]["message"]
    assert missing == [
        {
            "type": "error",
            "message": "No conversation 'loop-0123456789abcdef' to continue.",
            "hint": "Start a new one.",
        }
    ]


def test_scripts_are_written_freely_and_changes_wait_for_review(
    ws: Workspace, project: Path
) -> None:
    existing = project / "analysis" / "ndvi.py"
    existing.write_text(SCRIPT)
    model = Scripted(
        [
            _call("Write", "w", file_path="analysis/new.py", content=SCRIPT),
            _call("Read", "r", file_path=str(existing)),
            _call("Write", "x", file_path=str(project / "results.csv"), content="x"),
            _call("Edit", "e", file_path="analysis/ndvi.py", old_string="print", new_string="repr"),
            _call("Edit", "m", file_path="analysis/ndvi.py", old_string="nothing", new_string="x"),
        ],
        [TextPart("Done.")],
    )

    events = _answer(_agent(ws, project, model))

    told = model.returns()
    assert (project / "analysis" / "new.py").read_text() == SCRIPT
    assert told["r"] == SCRIPT
    assert "analysis" in told["x"] and not (project / "results.csv").exists()
    assert existing.read_text() == SCRIPT.replace("print", "repr")  # accepted below
    assert told["m"].startswith("old_string must appear exactly once")
    (proposal,) = [e for e in events if e["type"] == "proposal"]
    assert (proposal["kind"], proposal["path"], proposal["tool_use_id"]) == (
        "edit",
        "analysis/ndvi.py",
        "e",
    )
    assert (proposal["before"], proposal["after"]) == (SCRIPT, SCRIPT.replace("print", "repr"))
    results = {e["id"]: e["is_error"] for e in events if e["type"] == "tool_result"}
    assert results == {"w": False, "r": False, "x": True, "e": False, "m": True}
    shown = {e["id"]: e["input"].get("file_path") for e in events if e["type"] == "tool"}
    assert shown["r"] == "analysis/ndvi.py"  # relative to the project, as for Claude


def test_rejected_changes_and_runs_tell_the_model_why(ws: Workspace, project: Path) -> None:
    existing = project / "analysis" / "ndvi.py"
    existing.write_text(SCRIPT)
    model = Scripted(
        [
            _call("Write", "w", file_path=str(existing), content="print(1)\n"),
            _call("run", "r", path="analysis/ndvi.py", inputs={}),
            _call("run", "c", code=SCRIPT, inputs={}),
        ],
        [TextPart("What would you like instead?")],
    )

    events = _answer(_agent(ws, project, model), review="runs", reply=lambda _: (False, "no"))

    told = model.returns()
    assert told["w"] == (
        "The scientist rejected this change to analysis/ndvi.py; nothing was changed. "
        "Their note: no"
    )
    assert "rejected this run of analysis/ndvi.py" in told["r"]
    assert "path=" in told["c"]  # code= is refused before any review
    assert existing.read_text() == SCRIPT
    kinds = [e["kind"] for e in events if e["type"] == "proposal"]
    assert kinds == ["edit", "run"]


def test_scripts_run_by_path_with_their_outputs_checked(
    ws: Workspace, project: Path, ramp_tif: Path
) -> None:
    dem = load(ws, str(ramp_tif)).data_ref
    model = Scripted(
        [_call("Write", "w", file_path="analysis/double.py", content=DOUBLE)],
        [_call("run", "r", path="analysis/double.py", inputs={"dem": dem})],
        [TextPart("Doubled.")],
    )

    events = _answer(_agent(ws, project, model))

    (check,) = [e for e in events if e["type"] == "check"]
    assert (check["tool_use_id"], check["status"]) == ("r", "pass")
    assert '"status":"ok"' in model.returns()["r"]
    record = provenance.read(ws, check["data_ref"])
    assert record is not None  # the script is named by its absolute path, whatever the call gave
    arguments = record.call["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["path"] == str(project.resolve() / "analysis" / "double.py")


def test_calls_that_never_reach_a_tool_still_get_a_result(ws: Workspace, project: Path) -> None:
    model = Scripted(
        [_call("Bash", "b", command="ls"), ToolCallPart("load", '{"uri": ', tool_call_id="j")],
        [TextPart("Sorry.")],
    )

    events = _answer(_agent(ws, project, model))

    results = {e["id"]: e for e in events if e["type"] == "tool_result"}
    assert results["b"]["is_error"] and "Unknown tool name: 'Bash'" in results["b"]["text"]
    assert results["j"]["is_error"] and "Invalid JSON" in results["j"]["text"]
    assert [e["type"] for e in events][-2:] == ["text", "done"]


def test_previews_reach_the_model_as_pictures(ws: Workspace, project: Path, ramp_tif: Path) -> None:
    dem = load(ws, str(ramp_tif)).data_ref
    model = Scripted([_call("preview", "p", data_ref=dem, width=64)], [TextPart("A ramp.")])

    _answer(_agent(ws, project, model))

    request = model.requests[-1][-1]
    assert isinstance(request, ModelRequest)
    pictures = [
        c
        for p in request.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, list)
        for c in p.content
        if isinstance(c, BinaryContent)
    ]
    assert [p.media_type for p in pictures] == ["image/png"]


def test_unset_providers_and_failing_models_arrive_as_events(ws: Workspace, project: Path) -> None:
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("quota exceeded")

    unset = _answer(_agent(ws, project, Scripted(), GEMINI_API_KEY="g"))
    agent = Agent(
        ws,
        project=project,
        loop_model=lambda choice: FunctionModel(broken),
        loop_server=build_server,
        settings=lambda: Settings(env=KEYS),
    )
    failed = _answer(agent)

    assert unset[0]["message"] == "OpenAI is not set up."
    assert "OPENAI_API_KEY" in unset[0]["hint"]
    assert [e["type"] for e in failed] == ["session", "error"]
    assert failed[1]["message"] == "RuntimeError: quota exceeded"
    assert not (ws.root / "chats").exists()  # a failed answer is not kept


def test_a_providers_refusal_arrives_in_plain_words(ws: Workspace, project: Path) -> None:
    def refused(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError.for_category(
            ModelAuthenticationError,
            status_code=401,
            model_name="gpt-6-astra",
            body={"message": "Incorrect API key provided", "code": "invalid_api_key"},
        )

    agent = Agent(
        ws,
        project=project,
        loop_model=lambda choice: FunctionModel(refused),
        loop_server=build_server,
        settings=lambda: Settings(env=KEYS),
    )
    error = _answer(agent)[-1]

    assert error["message"] == "OpenAI did not accept the API key (401): Incorrect API key provided"
    assert "[openai]" in error["hint"] and "OPENAI_API_KEY" in error["hint"]


def _openai_reply(first: bool, uri: str) -> dict[str, Any]:
    """An OpenAI Responses API reply: a call to load, then an answer."""
    output = (
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "load",
            "arguments": json.dumps({"uri": uri}),
            "status": "completed",
        }
        if first
        else {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Loaded.", "annotations": []}],
        }
    )
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-6-astra",
        "output": [output],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": usage,
    }


def _gemini_reply(first: bool, uri: str) -> dict[str, Any]:
    """A Gemini generateContent reply: a call to load, then an answer."""
    part = (
        {"functionCall": {"name": "load", "args": {"uri": uri}}} if first else {"text": "Loaded."}
    )
    return {
        "candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
    }


def _openrouter_reply(first: bool, uri: str) -> dict[str, Any]:
    """An OpenRouter chat completion: a call to load, then an answer."""
    function = {"name": "load", "arguments": json.dumps({"uri": uri})}
    call = {"id": "call_1", "type": "function", "function": function}
    message = (
        {"role": "assistant", "content": None, "tool_calls": [call]}
        if first
        else {"role": "assistant", "content": "Loaded."}
    )
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen/qwen3.8-flash",
        "provider": "Alibaba",  # OpenRouter names the provider that served the request
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if first else "stop"}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


REPLIES = {
    "openai:gpt-6-astra": _openai_reply,
    "google:gemini-3.8-flash": _gemini_reply,
    "openrouter:qwen/qwen3.8-flash": _openrouter_reply,
}


def _client_model(model_id: str, http: httpx2.AsyncClient) -> Model:
    """The provider's own Pydantic AI model, sending its requests through `http`."""
    name = model_id.partition(":")[2]
    if model_id.startswith("openai:"):
        return OpenAIResponsesModel(name, provider=OpenAIProvider(api_key="k", http_client=http))
    if model_id.startswith("google:"):
        return GoogleModel(name, provider=GoogleProvider(api_key="k", http_client=http))
    return OpenRouterModel(name, provider=OpenRouterProvider(api_key="k", http_client=http))


@pytest.mark.parametrize("model_id", list(REPLIES))
def test_each_providers_own_client_carries_the_loci_tools(
    model_id: str, ws: Workspace, project: Path, ramp_tif: Path
) -> None:
    """The providers' request and reply formats, through Pydantic AI, against a mock of each
    provider's HTTP API: the closest these tests come to a live call without keys."""
    sent: list[str] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.content.decode())
        return httpx2.Response(200, json=REPLIES[model_id](len(sent) == 1, str(ramp_tif)))

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(reply))
    agent = Agent(
        ws,
        project=project,
        loop_model=lambda choice: _client_model(model_id, http),
        loop_server=build_server,
        settings=lambda: Settings(env={**KEYS, "OPENROUTER_API_KEY": "k"}),
    )

    events = _answer(agent, "Load the ramp", model=model_id)

    assert [e["type"] for e in events] == [
        "session",
        "tool",
        "tool_result",
        "check",
        "text",
        "done",
    ]
    tools = ["stac_search", "load", "inspect", "validate", "preview", "run", "lineage"]
    assert all(f'"{name}"' in sent[0] for name in [*tools, "Read", "Write", "Edit"])
    assert "the analyst inside loci" in sent[0] and "Data stays on disk" in sent[0]
    assert "loci validated raster-" in sent[1]  # the check went back to the model with the result


def test_each_provider_gets_its_own_client() -> None:
    settings = Settings(env={**KEYS, "OPENROUTER_API_KEY": "or-test"})

    openai = build_model(resolve("openai:gpt-6-astra", settings))
    gemini = build_model(resolve("google:gemini-3.8-flash", settings))
    routed = build_model(resolve("openrouter:qwen/qwen3.8-flash", settings))

    assert isinstance(openai, OpenAIResponsesModel) and openai.model_name == "gpt-6-astra"
    assert isinstance(gemini, GoogleModel) and gemini.model_name == "gemini-3.8-flash"
    assert isinstance(routed, OpenRouterModel) and routed.model_name == "qwen/qwen3.8-flash"
    with pytest.raises(LociError, match="does not run Claude"):
        build_model(resolve("claude:opus", settings))
