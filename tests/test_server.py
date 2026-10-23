"""The MCP server, through a real MCP client: in-process, and over stdio as Claude Code runs it."""

import base64
import json
import sys
from collections.abc import Awaitable, Callable
from inspect import signature
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ImageContent, TextContent

from loci.models import RefSummary, RunResult
from loci.server import TOOLS, build_server, call
from loci.tools.run import MAX_TIMEOUT
from loci.workspace import Workspace

type Session = Callable[[Client], Awaitable[Any]]


def _with_client(server: Any, session: Session) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await session(client)

    return anyio.run(main)


def _text(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_every_tool_is_listed_with_its_python_arguments(ws: Workspace) -> None:
    async def session(client: Client) -> Any:
        return (await client.list_tools()).tools

    listed = {t.name: t for t in _with_client(build_server(ws), session)}

    assert sorted(listed) == sorted(tool.__name__ for tool, _, _ in TOOLS)
    for tool, title, _ in TOOLS:
        arguments = list(signature(tool).parameters)[1:]  # all but the workspace
        assert list(listed[tool.__name__].input_schema["properties"]) == arguments
        assert listed[tool.__name__].title == title
        assert listed[tool.__name__].output_schema is not None
    assert listed["load"].input_schema["required"] == ["uri"]


def test_results_come_back_as_compact_json_and_structured_content(
    ws: Workspace, ramp_tif: Path
) -> None:
    async def session(client: Client) -> tuple[CallToolResult, CallToolResult]:
        loaded = await client.call_tool("load", {"uri": str(ramp_tif)})
        ref = loaded.structured_content["data_ref"] if loaded.structured_content else ""
        return loaded, await client.call_tool("inspect", {"data_ref": ref})

    loaded, inspected = _with_client(build_server(ws), session)

    assert not loaded.is_error and not inspected.is_error
    summary = RefSummary.model_validate(inspected.structured_content)
    assert summary.data_ref == RefSummary.model_validate_json(_text(loaded)).data_ref
    assert "null" not in _text(inspected)  # empty fields are left out of the text
    assert ws.record(summary.data_ref).source.uri == str(ramp_tif)


def test_errors_reach_the_agent_with_their_hints(ws: Workspace) -> None:
    async def session(client: Client) -> CallToolResult:
        return await client.call_tool("inspect", {"data_ref": "raster-00000000"})

    result = _with_client(build_server(ws), session)

    assert result.is_error
    assert "No data_ref 'raster-00000000'" in _text(result)
    assert "Hint:" in _text(result)


def test_unexpected_errors_name_their_type(ws: Workspace) -> None:
    def broken(ws: Workspace) -> RefSummary:
        raise ValueError("bad pixels")

    with pytest.raises(ToolError, match="ValueError: bad pixels"):
        call(broken, ws, {})


def test_preview_returns_its_picture(ws: Workspace, ramp_tif: Path) -> None:
    async def session(client: Client) -> CallToolResult:
        loaded = await client.call_tool("load", {"uri": str(ramp_tif)})
        ref = loaded.structured_content["data_ref"] if loaded.structured_content else ""
        return await client.call_tool("preview", {"data_ref": ref, "width": 128})

    result = _with_client(build_server(ws), session)
    image = result.content[1]

    assert isinstance(image, ImageContent) and image.mime_type == "image/png"
    assert base64.b64decode(image.data).startswith(b"\x89PNG")


def test_run_through_mcp_registers_and_validates_outputs(ws: Workspace, ramp_tif: Path) -> None:
    code = "from loci import script\nscript.save(script.read('dem') * 2, 'doubled.tif')\n"

    async def session(client: Client) -> CallToolResult:
        loaded = await client.call_tool("load", {"uri": str(ramp_tif)})
        ref = loaded.structured_content["data_ref"] if loaded.structured_content else ""
        return await client.call_tool("run", {"code": code, "inputs": {"dem": ref}})

    result = RunResult.model_validate(_with_client(build_server(ws), session).structured_content)

    assert result.status == "ok"
    assert result.outputs[0].validation == "pass"


def test_the_server_runs_over_stdio(ws: Workspace, ramp_tif: Path) -> None:
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "loci.server"], env={"LOCI_WORKSPACE": str(ws.root)}
    )

    async def session(client: Client) -> CallToolResult:
        return await client.call_tool("load", {"uri": str(ramp_tif)})

    result = _with_client(server, session)
    ref = json.loads(_text(result))["data_ref"]

    assert ws.record(ref).source.uri == str(ramp_tif)  # same workspace as the caller


def test_project_config_starts_the_server_and_hook() -> None:
    root = Path(__file__).parents[1]
    mcp = json.loads((root / ".mcp.json").read_text())["mcpServers"]["loci"]
    settings = json.loads((root / ".claude" / "settings.json").read_text())
    (hook_group,) = settings["hooks"]["PostToolUse"]
    pyproject = (root / "pyproject.toml").read_text()

    assert mcp["args"][-1] == "loci-mcp" and 'loci-mcp = "loci.server:main"' in pyproject
    # Claude Code must wait longer than run's own limit, so a slow script ends with run's
    # structured timeout instead of an abandoned call.
    assert mcp["timeout"] > MAX_TIMEOUT * 1000
    assert hook_group["hooks"][0]["command"].endswith("loci-hook")
    assert 'loci-hook = "loci.hooks:main"' in pyproject
    assert settings["env"]["LOCI_VALIDATION_HOOK"] == "report"
