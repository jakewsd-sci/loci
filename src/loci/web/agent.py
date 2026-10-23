"""The agent in the app: one Claude conversation over the loci tools, streamed as events.

Each message runs a Claude Agent SDK query with the loci MCP server as the only tools, on this
app's workspace, so every data_ref the agent makes appears on the map. The validation hook runs
in process after load, stac_search and run, exactly as hooks.py does for Claude Code, and its
results are streamed to the app too. A session id lets later messages continue the conversation.
"""

import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from loci import hooks
from loci.workspace import ROOT_ENV, Workspace

MODELS = [
    {"id": "opus", "label": "Opus", "note": "Most capable"},
    {"id": "sonnet", "label": "Sonnet", "note": "Fast and capable"},
    {"id": "haiku", "label": "Haiku", "note": "Fastest"},
]
DEFAULT_MODEL = "opus"
MAX_TURNS = 40
VALIDATED_TOOLS = "^mcp__loci__(load|stac_search|run)$"
TOOL_PREFIX = "mcp__loci__"
SYSTEM_PROMPT = """\
You are the analyst inside loci, a map-first workspace for geospatial science. The person you
work with sees a map, a list of layers and your messages. Every data_ref you create appears on
their map, with its validation status, as soon as you make it.

Use the loci tools for all data work. Keep answers short and concrete: what you did, the
result in physical units, and what limits how far it can be trusted. Mention data_refs so the
person can find them on the map. Pass on warnings and failed checks; never hide them. Write
analysis scripts for run so that they read inputs with loci.script.read and save results with
loci.script.save, and say which file each result came from.
"""

Event = dict[str, Any]
Query = Callable[..., AsyncIterator[Any]]


@dataclass
class Agent:
    """Runs messages against the loci tools for one workspace."""

    ws: Workspace
    run_query: Query = query
    mode: str = hooks.DEFAULT_MODE
    _checks: list[Event] = field(default_factory=list)

    async def send(
        self, message: str, *, model: str = DEFAULT_MODEL, session_id: str | None = None
    ) -> AsyncIterator[Event]:
        """Events for one message: text, tool calls and results, checks, then done."""
        if model not in {m["id"] for m in MODELS}:
            yield {"type": "error", "message": f"Unknown model {model!r}."}
            return
        options = self.options(model=model, session_id=session_id)
        try:
            async for item in self.run_query(prompt=message, options=options):
                for event in self._events(item):
                    yield event
                while self._checks:
                    yield self._checks.pop(0)
        except Exception as e:  # report it in the conversation rather than dropping the stream
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}

    def options(self, *, model: str, session_id: str | None) -> ClaudeAgentOptions:
        server = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "loci.server"],
            "env": {ROOT_ENV: str(self.ws.root)},
        }
        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"loci": server},  # type: ignore[dict-item]
            strict_mcp_config=True,
            tools=[],
            allowed_tools=["mcp__loci"],
            setting_sources=[],
            hooks={"PostToolUse": [HookMatcher(matcher=VALIDATED_TOOLS, hooks=[self._check])]},  # type: ignore[list-item]
            model=model,
            resume=session_id,
            max_turns=MAX_TURNS,
            cwd=str(Path(self.ws.root).parent),
        )

    async def _check(
        self, hook_input: Any, tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """The validation hook, in process: validate new data_refs and tell the app."""
        event = dict(hook_input)
        output = await anyio.to_thread.run_sync(lambda: hooks.respond(event, self.ws, self.mode))
        if not output:
            return {}
        text = output.get("reason") or output.get("hookSpecificOutput", {}).get(
            "additionalContext", ""
        )
        for line in str(text).splitlines():
            if line.startswith("loci validated "):
                ref, _, rest = line.removeprefix("loci validated ").partition(" automatically: ")
                status = rest.split(" ", 1)[0]
                self._checks.append(
                    {"type": "check", "tool_use_id": tool_use_id, "data_ref": ref, "status": status}
                )
        return output

    def _events(self, item: Any) -> list[Event]:
        if isinstance(item, SystemMessage) and item.subtype == "init":
            return [{"type": "session", "session_id": item.data.get("session_id")}]
        if isinstance(item, AssistantMessage):
            events: list[Event] = []
            for block in item.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    events.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    name = block.name.removeprefix(TOOL_PREFIX)
                    events.append(
                        {"type": "tool", "id": block.id, "name": name, "input": block.input}
                    )
            return events
        if isinstance(item, UserMessage) and isinstance(item.content, list):
            return [
                {
                    "type": "tool_result",
                    "id": block.tool_use_id,
                    "is_error": bool(block.is_error),
                    "text": _text(block.content),
                }
                for block in item.content
                if isinstance(block, ToolResultBlock)
            ]
        if isinstance(item, ResultMessage):
            return [
                {
                    "type": "done",
                    "session_id": item.session_id,
                    "turns": item.num_turns,
                    "cost_usd": item.total_cost_usd,
                    "seconds": round(item.duration_ms / 1000, 1),
                    "is_error": item.is_error,
                }
            ]
        return []


def _text(content: str | list[dict[str, Any]] | None, limit: int = 4000) -> str:
    """A tool result's text, shortened; images and other blocks are left out."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:limit]
    return "\n".join(str(b.get("text", "")) for b in content if b.get("type") == "text")[:limit]
