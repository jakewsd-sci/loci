"""loci's own agent loop, for the models Claude Code does not run (D-048).

OpenAI, Google Gemini and OpenRouter models answer through Pydantic AI with what Claude has in
Claude Code (agent.py): the same instructions, the loci MCP server's tools through an MCP
client, and Read, Write and Edit for the scripts under analysis/, implemented here. Every call
goes through the agent's permission step, so changes to scripts, and with review="runs" every
run, wait for the scientist; new data_refs from load, stac_search and run are validated as the
hook does. A conversation's messages are kept in the workspace under chats/, so its session id
continues it, whichever of these models answers next.
"""

import base64
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pydantic_ai
from claude_agent_sdk import PermissionResultDeny
from mcp import Client, StdioServerParameters
from mcp_types import ImageContent, TextContent
from mcp_types import Tool as ServerTool
from pydantic_ai import Agent as Runner
from pydantic_ai import BinaryContent, RunContext, Tool, ToolReturn
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelHTTPError,
    ModelNotFoundError,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from loci.errors import LociError
from loci.web.providers import Choice, setup_hint
from loci.web.turn import (
    FILE_TOOLS,
    MAX_TURNS,
    SYSTEM_PROMPT,
    TOOL_PREFIX,
    VALIDATED,
    Event,
    Turn,
    clip,
    hook_note,
    proposed,
)
from loci.workspace import ROOT_ENV, Workspace

if TYPE_CHECKING:
    from loci.web.agent import Agent

pydantic_ai.BANNER_ENABLED = False  # no first-run banner in the app's output

SESSION = re.compile(r"loop-[0-9a-f]{16}")  # Claude Code's session ids are UUIDs
CHATS_DIR = "chats"
RETRIES = 3  # malformed tool calls a model may correct before the turn fails
APP_TITLE = "loci"  # how loci appears in OpenRouter's activity list

# Read, Write and Edit as Claude Code offers them, for the scripts under analysis/ only.
FILE_TOOL_SPECS: dict[str, tuple[str, dict[str, Any]]] = {
    "Read": (
        "Read a script under analysis/ and return its text.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The script, such as analysis/ndvi.py.",
                }
            },
            "required": ["file_path"],
        },
    ),
    "Write": (
        "Create a script under analysis/, or replace one with new text. Replacing an existing "
        "script waits for the scientist to review the change.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The script, such as analysis/ndvi.py.",
                },
                "content": {"type": "string", "description": "The script's full text."},
            },
            "required": ["file_path", "content"],
        },
    ),
    "Edit": (
        "Replace old_string with new_string in a script under analysis/. old_string must "
        "appear exactly once, unless replace_all is true. The change waits for the scientist "
        "to review it.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The script, such as analysis/ndvi.py.",
                },
                "old_string": {"type": "string", "description": "The exact text to replace."},
                "new_string": {"type": "string", "description": "The text to put in its place."},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence of old_string.",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    ),
}


def is_session(session_id: str) -> bool:
    """Whether a session id names one of this loop's conversations."""
    return SESSION.fullmatch(session_id) is not None


def build_model(choice: Choice) -> Model:
    """The Pydantic AI model for one of the providers this loop runs."""
    key = choice.api_key
    if choice.provider.id == "openai":
        return OpenAIResponsesModel(choice.model, provider=OpenAIProvider(api_key=key))
    if choice.provider.id == "google":
        return GoogleModel(choice.model, provider=GoogleProvider(api_key=key))
    if choice.provider.id == "openrouter":
        provider = OpenRouterProvider(api_key=key, app_title=APP_TITLE)
        return OpenRouterModel(choice.model, provider=provider)
    raise LociError(f"loci's own loop does not run {choice.provider.label} models.")


def stdio_server(ws: Workspace) -> StdioServerParameters:
    """The loci MCP server on this workspace, started as Claude Code starts it."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "loci.server"],
        env={**os.environ, ROOT_ENV: str(ws.root)},
    )


async def answer(
    host: "Agent", turn: Turn, message: str, choice: Choice, session_id: str | None
) -> None:
    """Answer one message, putting its events on the turn's queue.

    The conversation so far is read from the workspace and saved again once the answer is
    complete; an answer that fails or is abandoned is not added to it.
    """
    started = time.monotonic()
    session = session_id or f"loop-{secrets.token_hex(8)}"
    history = read_history(host.ws, session) if session_id else []
    model = host.loop_model(choice)
    host.scripts.mkdir(parents=True, exist_ok=True)
    await turn.events.put({"type": "session", "session_id": session})
    async with Client(host.loop_server(host.ws)) as client:
        listed = await client.list_tools()
        tools = _Tools(host, turn)
        offered = [tools.server_tool(client, spec) for spec in listed.tools]
        offered += [tools.file_tool(name) for name in FILE_TOOLS]
        instructions = SYSTEM_PROMPT.format(scripts=host.scripts)
        if client.instructions:
            instructions += "\n" + client.instructions
        runner = Runner(model, instructions=instructions, tools=offered, retries=RETRIES)
        limits = UsageLimits(request_limit=MAX_TURNS)
        try:
            async with runner.iter(message, message_history=history, usage_limits=limits) as run:
                async for node in run:
                    if Runner.is_call_tools_node(node):
                        events = _events(host, node.model_response)
                    elif Runner.is_model_request_node(node):
                        events = tools.unreported(node.request)
                    else:
                        continue
                    for event in events:
                        await turn.events.put(event)
        except ModelAPIError as e:
            raise _refused(e, choice, setup_hint(choice.provider, host.settings())) from e
        if run.result is None:
            raise LociError("The model stopped before it answered.")
        save_history(host.ws, session, run.result.all_messages())
        usage = run.usage
    await turn.events.put(
        {
            "type": "done",
            "session_id": session,
            "turns": usage.requests,
            "cost_usd": None if usage.cost is None else float(usage.cost),  # None: not priced
            "seconds": round(time.monotonic() - started, 1),
            "is_error": False,
            "tokens": {"input": usage.input_tokens, "output": usage.output_tokens},
        }
    )


def read_history(ws: Workspace, session: str) -> list[ModelMessage]:
    """The messages of a conversation so far."""
    path = _chat(ws, session)
    if not path.exists():
        raise LociError(f"No conversation {session!r} to continue.", hint="Start a new one.")
    return list(ModelMessagesTypeAdapter.validate_json(path.read_bytes()))


def save_history(ws: Workspace, session: str, messages: list[ModelMessage]) -> None:
    path = _chat(ws, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(ModelMessagesTypeAdapter.dump_json(messages))
    os.replace(temporary, path)


def _chat(ws: Workspace, session: str) -> Path:
    if not is_session(session):
        raise LociError(f"{session!r} is not a conversation of loci's own loop.")
    return ws.root / CHATS_DIR / f"{session}.json"


def _refused(e: ModelAPIError, choice: Choice, setup: str) -> LociError:
    """A provider's error in words the scientist can act on."""
    label = choice.provider.label
    said = _said(e.body) or str(e)
    status = f" ({e.status_code})" if isinstance(e, ModelHTTPError) else ""
    if isinstance(e, ModelAuthenticationError):
        return LociError(f"{label} did not accept the API key{status}: {said}", hint=setup)
    if isinstance(e, ModelNotFoundError):
        return LociError(
            f"{label} has no model {choice.model!r}{status}: {said}",
            hint=f"Choose another model, or list yours under [{choice.provider.id}] models in "
            "the config file.",
        )
    if isinstance(e, ModelConnectionError):
        return LociError(f"Could not reach {label}: {said}")
    return LociError(f"{label} could not answer{status}: {said}")


def _said(body: object) -> str:
    """The provider's own message, from an error body of any of the usual shapes."""
    if isinstance(body, dict):
        inner = body.get("error")
        message = (inner if isinstance(inner, dict) else body).get("message")
        if isinstance(message, str):
            return message
    return "" if body is None else str(body)[:300]


def _events(host: "Agent", response: ModelResponse) -> list[Event]:
    """The app's events for a model response: its text and the tool calls it asks for."""
    events: list[Event] = []
    for part in response.parts:
        if isinstance(part, TextPart) and part.content.strip():
            events.append({"type": "text", "text": part.content})
        elif isinstance(part, ToolCallPart):
            shown = host.shown(part.args_as_dict())
            events.append(
                {"type": "tool", "id": part.tool_call_id, "name": part.tool_name, "input": shown}
            )
    return events


@dataclass
class _Tools:
    """The loop's tools for one answer, and which of their results the app has been told."""

    host: "Agent"
    turn: Turn
    reported: set[str] = field(default_factory=set)

    def server_tool(self, client: Client, spec: ServerTool) -> Tool[None]:
        """A loci MCP tool, through the permission step and, for new data_refs, validation."""
        host, turn = self.host, self.turn
        name = TOOL_PREFIX + spec.name  # as Claude Code names it, as the permission step expects

        async def call(ctx: RunContext[None], **arguments: Any) -> str | ToolReturn:
            allowed = await host.permit(turn, name, arguments, ctx.tool_call_id)
            if isinstance(allowed, PermissionResultDeny):
                return await self.report(ctx.tool_call_id, allowed.message, error=True)
            arguments = allowed.updated_input or arguments
            result = await client.call_tool(spec.name, arguments)
            text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
            note = ""
            if spec.name in VALIDATED and not result.is_error:
                hook_input = {
                    "hook_event_name": "PostToolUse",
                    "tool_name": name,
                    "tool_input": arguments,
                    "tool_response": text,
                }
                note = hook_note(await host.check(turn, hook_input, ctx.tool_call_id))
            await self.report(ctx.tool_call_id, text, error=bool(result.is_error))
            told = f"{text}\n\n{note}" if note else text  # the hook's report goes to the model
            images = [
                BinaryContent(base64.b64decode(c.data), media_type=c.mime_type)
                for c in result.content
                if isinstance(c, ImageContent)
            ]
            return ToolReturn(return_value=told, content=images) if images else told

        return Tool.from_schema(
            call,
            name=spec.name,
            description=spec.description,
            json_schema=spec.input_schema,
            takes_ctx=True,
            sequential=True,
        )

    def file_tool(self, name: str) -> Tool[None]:
        """Read, Write or Edit for a script under analysis/, through the permission step."""
        host, turn = self.host, self.turn
        description, schema = FILE_TOOL_SPECS[name]

        async def call(ctx: RunContext[None], **arguments: Any) -> str:
            allowed = await host.permit(turn, name, arguments, ctx.tool_call_id)
            if isinstance(allowed, PermissionResultDeny):
                return await self.report(ctx.tool_call_id, allowed.message, error=True)
            file = host.script(arguments.get("file_path"))
            assert file is not None  # the permission step allows scripts under analysis/ only
            text, error = _apply(name, file, host.relative(file), arguments)
            return await self.report(ctx.tool_call_id, text, error=error)

        return Tool.from_schema(
            call,
            name=name,
            description=description,
            json_schema=schema,
            takes_ctx=True,
            sequential=True,
        )

    async def report(self, call_id: str | None, text: str, *, error: bool) -> str:
        """Tell the app a tool's result, then the checks it set off; the text goes to the model."""
        if call_id is not None:
            self.reported.add(call_id)
        await self.turn.events.put(
            {"type": "tool_result", "id": call_id, "is_error": error, "text": clip(text)}
        )
        while self.turn.checks:
            await self.turn.events.put(self.turn.checks.pop(0))
        return text

    def unreported(self, request: ModelRequest) -> list[Event]:
        """Results Pydantic AI gave the model itself, for calls that never reached a tool: an
        unknown tool, or arguments that are not valid JSON."""
        events: list[Event] = []
        for part in request.parts:
            if isinstance(part, ToolReturnPart):
                text = part.model_response_str()
            elif isinstance(part, RetryPromptPart) and part.tool_name is not None:
                text = part.model_response()
            else:
                continue
            if part.tool_call_id not in self.reported:
                self.reported.add(part.tool_call_id)
                events.append(
                    {
                        "type": "tool_result",
                        "id": part.tool_call_id,
                        "is_error": True,
                        "text": clip(text),
                    }
                )
        return events


def _apply(name: str, file: Path, shown: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Carry out an allowed Read, Write or Edit: what to tell the model, and whether it failed."""
    if name == "Read":
        if not file.exists():
            return f"{shown} does not exist.", True
        return file.read_text() or f"{shown} is empty.", False
    if name == "Edit" and not file.exists():
        return f"{shown} does not exist; create it with Write.", True
    before = file.read_text() if file.exists() else ""
    after = proposed(name, before, arguments)
    if after is None:
        if name == "Write":
            return "Give the script's full text as content.", True
        return (
            f"old_string must appear exactly once in {shown}, or set replace_all to change "
            "every occurrence.",
            True,
        )
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(after)
    return f"{'Wrote' if name == 'Write' else 'Edited'} {shown}.", False
