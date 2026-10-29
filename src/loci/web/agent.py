"""The agent in the app: one Claude conversation over the loci tools and the project's scripts.

Each message runs a Claude Agent SDK query on this app's workspace, so every data_ref the agent
makes appears on the map. Its tools are the loci MCP server plus Read, Write and Edit, limited to
.py files under the project's analysis/ folder (D-047): every analysis is a script the scientist
can see, edit and run again, and run takes it by path. A change to an existing script waits for
the scientist to accept or reject it as a diff in the app; with review="runs", so does every
run. The validation hook runs in process after load, stac_search and run, as hooks.py does for
Claude Code. All of it streams to the app as events; a session id continues the conversation.
"""

import asyncio
import secrets
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from loci import hooks
from loci.web.project import SCRIPTS_DIR
from loci.workspace import ROOT_ENV, Workspace

MODELS = [
    {"id": "opus", "label": "Opus", "note": "Most capable"},
    {"id": "sonnet", "label": "Sonnet", "note": "Fast and capable"},
    {"id": "haiku", "label": "Haiku", "note": "Fastest"},
]
DEFAULT_MODEL = "opus"
REVIEW_MODES = ("changes", "runs")  # what waits for the scientist: script changes, or runs too
DEFAULT_REVIEW = "changes"
MAX_TURNS = 40
DECISION_TIMEOUT = 1800.0  # seconds to wait for an answer before taking it as a rejection
VALIDATED_TOOLS = "^mcp__loci__(load|stac_search|run)$"
FILE_TOOLS = ["Read", "Write", "Edit"]
GUARDED_TOOLS = "^(Read|Write|Edit)$"
TOOL_PREFIX = "mcp__loci__"
RUN_TOOL = f"{TOOL_PREFIX}run"
SYSTEM_PROMPT = """\
You are the analyst inside loci, a map-first workspace for geospatial science. The person you
work with sees a map, a list of layers, a code editor and your messages. Every data_ref you
create appears on their map, with its validation status, as soon as you make it.

Use the loci tools for all data work. Write every analysis as a Python script in {scripts}
with Write, then run it with run(path="{scripts}/<name>.py", inputs={{...}}); never pass code=
to run. To change an analysis, Edit its script and run it again rather than starting a new file.
The scientist sees each script in the editor and reviews every change to an existing one as a
diff before it is made: if they reject it, ask what they would like instead. Scripts read their
inputs with loci.script.read and save results with loci.script.save. Read and write only those
scripts; reach data through the loci tools.

Keep answers short and concrete: what you did, the result in physical units, and what limits
how far it can be trusted. Mention data_refs and script names so the person can find them. Pass
on warnings and failed checks; never hide them. End each answer with a line "Next:" followed by
up to three short follow-up questions the person might ask, each as a "- " bullet.
"""

Event = dict[str, Any]
Query = Callable[..., AsyncIterator[Any]]
Permission = PermissionResultAllow | PermissionResultDeny


@dataclass
class _Turn:
    """One message being answered: where its events go, and what it is waiting on."""

    events: "asyncio.Queue[Event | None]"
    review: str
    checks: list[Event] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)


@dataclass
class Agent:
    """Runs messages against the loci tools and the project's scripts for one workspace."""

    ws: Workspace
    project: Path | None = None  # holds analysis/; defaults to the workspace's parent folder
    run_query: Query = query
    mode: str = hooks.DEFAULT_MODE
    _pending: dict[str, "asyncio.Future[tuple[bool, str]]"] = field(default_factory=dict)

    @property
    def folder(self) -> Path:
        return (self.project or Path(self.ws.root).parent).resolve()

    @property
    def scripts(self) -> Path:
        return self.folder / SCRIPTS_DIR

    async def send(
        self,
        message: str,
        *,
        model: str = DEFAULT_MODEL,
        session_id: str | None = None,
        review: str = DEFAULT_REVIEW,
    ) -> AsyncGenerator[Event]:
        """Events for one message: text, tool calls and results, checks, reviews, then done."""
        if model not in {m["id"] for m in MODELS}:
            yield {"type": "error", "message": f"Unknown model {model!r}."}
            return
        if review not in REVIEW_MODES:
            yield {"type": "error", "message": f"Unknown review mode {review!r}."}
            return
        turn = _Turn(asyncio.Queue(), review)
        options = self.options(model=model, session_id=session_id, turn=turn)
        # The query runs beside this generator, so a review can reach the app while the agent
        # is paused waiting for its answer.
        task = asyncio.create_task(self._pump(message, options, turn))
        try:
            while (event := await turn.events.get()) is not None:
                yield event
        finally:
            task.cancel()
            for proposal in turn.waiting:
                pending = self._pending.pop(proposal, None)
                if pending is not None and not pending.done():
                    pending.cancel()

    def decide(self, proposal: str, accepted: bool, note: str = "") -> bool:
        """The scientist's answer to a review; False if nothing is waiting under that id."""
        pending = self._pending.get(proposal)
        if pending is None or pending.done():
            return False
        pending.set_result((accepted, note.strip()))
        return True

    def options(
        self, *, model: str, session_id: str | None, turn: _Turn | None = None
    ) -> ClaudeAgentOptions:
        turn = turn or _Turn(asyncio.Queue(), DEFAULT_REVIEW)
        self.scripts.mkdir(parents=True, exist_ok=True)
        server = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "loci.server"],
            "env": {ROOT_ENV: str(self.ws.root)},
        }

        async def permit(
            name: str, arguments: dict[str, Any], context: ToolPermissionContext
        ) -> Permission:
            return await self._permit(turn, name, arguments, context.tool_use_id)

        async def check(hook_input: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
            return await self._check(turn, hook_input, tool_use_id)

        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT.format(scripts=self.scripts),
            mcp_servers={"loci": server},  # type: ignore[dict-item]
            strict_mcp_config=True,
            tools=FILE_TOOLS,
            # Nothing is allowed up front: every call goes through permit, which decides.
            allowed_tools=[],
            can_use_tool=permit,
            setting_sources=[],
            hooks={
                "PreToolUse": [HookMatcher(matcher=GUARDED_TOOLS, hooks=[self._guard])],  # type: ignore[list-item]
                "PostToolUse": [HookMatcher(matcher=VALIDATED_TOOLS, hooks=[check])],  # type: ignore[list-item]
            },
            model=model,
            resume=session_id,
            max_turns=MAX_TURNS,
            cwd=str(self.folder),
        )

    async def _pump(self, message: str, options: ClaudeAgentOptions, turn: _Turn) -> None:
        """Run the query and put its events, then the checks they set off, on the turn's queue."""
        try:
            async for item in self.run_query(prompt=message, options=options):
                for event in self._events(item):
                    await turn.events.put(event)
                while turn.checks:
                    await turn.events.put(turn.checks.pop(0))
        except Exception as e:  # report it in the conversation rather than dropping the stream
            await turn.events.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await turn.events.put(None)

    async def _permit(
        self, turn: _Turn, name: str, arguments: dict[str, Any], tool_use_id: str | None
    ) -> Permission:
        """Whether a tool call may go ahead, asking the scientist where that is their call."""
        if name == RUN_TOOL:
            return await self._permit_run(turn, arguments, tool_use_id)
        if name.startswith(TOOL_PREFIX):
            return PermissionResultAllow()
        if name not in FILE_TOOLS:
            return PermissionResultDeny(message=f"{name} is not available here.")
        file = self._script(arguments.get("file_path"))
        if file is None:
            return PermissionResultDeny(message=self._outside())
        if name == "Read" or not file.exists():
            return PermissionResultAllow()  # reading a script, or writing a new one
        before = file.read_text()
        after = _proposed(name, before, arguments)
        if after is None or after == before:
            return PermissionResultAllow()  # nothing to review; the tool reports any error itself
        proposal = {"kind": "edit", "path": self._relative(file), "before": before, "after": after}
        return await self._ask(turn, proposal, tool_use_id, "change to " + self._relative(file))

    async def _permit_run(
        self, turn: _Turn, arguments: dict[str, Any], tool_use_id: str | None
    ) -> Permission:
        if arguments.get("code") is not None:
            return PermissionResultDeny(
                message=f"Save the script in {self.scripts} with Write and run it with path=, "
                "so the scientist can see and edit it."
            )
        file = self._script(arguments.get("path"))
        if file is None or not file.exists():
            return PermissionResultDeny(message=f"Run a script that exists in {self.scripts}.")
        updated = {**arguments, "path": str(file)}  # absolute, whatever the call gave
        if turn.review == "runs":
            proposal = {
                "kind": "run",
                "path": self._relative(file),
                "code": file.read_text(),
                "inputs": arguments.get("inputs") or {},
                "parameters": arguments.get("parameters"),
            }
            answer = await self._ask(turn, proposal, tool_use_id, "run of " + self._relative(file))
            if isinstance(answer, PermissionResultDeny):
                return answer
        return PermissionResultAllow(updated_input=updated)

    async def _ask(
        self, turn: _Turn, proposal: Event, tool_use_id: str | None, what: str
    ) -> Permission:
        """Send a review to the app and wait for the scientist's answer."""
        key = f"p-{secrets.token_hex(4)}"
        waiting: asyncio.Future[tuple[bool, str]] = asyncio.get_running_loop().create_future()
        self._pending[key] = waiting
        turn.waiting.append(key)
        await turn.events.put(
            {"type": "proposal", "id": key, "tool_use_id": tool_use_id, **proposal}
        )
        try:
            accepted, note = await asyncio.wait_for(waiting, DECISION_TIMEOUT)
        except TimeoutError:
            accepted, note = False, "no answer came in time"
        finally:
            self._pending.pop(key, None)
        await turn.events.put({"type": "decision", "id": key, "accepted": accepted})
        if accepted:
            return PermissionResultAllow()
        message = f"The scientist rejected this {what}; nothing was changed."
        return PermissionResultDeny(message=f"{message} Their note: {note}" if note else message)

    async def _guard(
        self, hook_input: Any, tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Before any Read, Write or Edit: only .py files under analysis/, whatever allows it."""
        arguments = dict(hook_input).get("tool_input") or {}
        if self._script(arguments.get("file_path")) is not None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": self._outside(),
            }
        }

    async def _check(self, turn: _Turn, hook_input: Any, tool_use_id: str | None) -> dict[str, Any]:
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
                turn.checks.append(
                    {"type": "check", "tool_use_id": tool_use_id, "data_ref": ref, "status": status}
                )
        return output

    def _script(self, path: object) -> Path | None:
        """The file a path names if it is a .py file under analysis/ (relative to the project)."""
        if not isinstance(path, str) or not path:
            return None
        file = (self.folder / Path(path).expanduser()).resolve()
        if file.suffix != ".py" or not file.is_relative_to(self.scripts):
            return None
        return file

    def _relative(self, file: Path) -> str:
        return str(file.relative_to(self.folder))

    def _outside(self) -> str:
        return (
            f"Only .py scripts under {self.scripts} can be read or written here; reach data "
            "through the loci tools."
        )

    def _events(self, item: Any) -> list[Event]:
        if isinstance(item, SystemMessage) and item.subtype == "init":
            return [{"type": "session", "session_id": item.data.get("session_id")}]
        if isinstance(item, AssistantMessage):
            events: list[Event] = []
            for block in item.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    events.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    events.append(
                        {
                            "type": "tool",
                            "id": block.id,
                            "name": block.name.removeprefix(TOOL_PREFIX),
                            "input": self._shown(block.input),
                        }
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

    def _shown(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A tool call's arguments as the app shows them: script paths relative to the project."""
        shown = dict(arguments)
        for key in ("file_path", "path"):
            file = self._script(shown.get(key))
            if file is not None:
                shown[key] = self._relative(file)
        return shown


def _proposed(name: str, before: str, arguments: dict[str, Any]) -> str | None:
    """A script's text after a Write or Edit, or None if the edit cannot apply as given."""
    if name == "Write":
        content = arguments.get("content")
        return content if isinstance(content, str) else None
    old, new = arguments.get("old_string"), arguments.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        return None
    if arguments.get("replace_all"):
        return before.replace(old, new) if old in before else None
    return before.replace(old, new, 1) if before.count(old) == 1 else None


def _text(content: str | list[dict[str, Any]] | None, limit: int = 4000) -> str:
    """A tool result's text, shortened; images and other blocks are left out."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:limit]
    return "\n".join(str(b.get("text", "")) for b in content if b.get("type") == "text")[:limit]
