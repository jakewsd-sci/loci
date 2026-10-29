"""The agent in the app: one conversation over the loci tools and the project's scripts.

Each message is answered on this app's workspace, so every data_ref the agent makes appears on
the map. Claude models run in Claude Code through the Claude Agent SDK; other providers' models
run in loci's own loop (loop.py, D-048) with the same tools, instructions, reviews and events.
The tools are the loci MCP server plus Read, Write and Edit, limited to .py files under the
project's analysis/ folder (D-047): every analysis is a script the scientist can see, edit and
run again, and run takes it by path. A change to an existing script waits for the scientist to
accept or reject it as a diff in the app; with review="runs", so does every run. New data_refs
from load, stac_search and run are validated in process, as hooks.py does for Claude Code. All
of it streams to the app as events; a session id continues the conversation.
"""

import asyncio
import secrets
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
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
from pydantic_ai.models import Model

from loci import hooks
from loci.errors import LociError
from loci.web import loop
from loci.web.project import SCRIPTS_DIR
from loci.web.providers import DEFAULT_MODEL, Choice, Settings, load_settings, resolve
from loci.web.turn import (
    DEFAULT_REVIEW,
    FILE_TOOLS,
    MAX_TURNS,
    REVIEW_MODES,
    RUN_TOOL,
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

DECISION_TIMEOUT = 1800.0  # seconds to wait for an answer before taking it as a rejection
VALIDATED_TOOLS = f"^{TOOL_PREFIX}({'|'.join(VALIDATED)})$"
GUARDED_TOOLS = f"^({'|'.join(FILE_TOOLS)})$"

Query = Callable[..., AsyncIterator[Any]]
Permission = PermissionResultAllow | PermissionResultDeny


@dataclass
class Agent:
    """Runs messages against the loci tools and the project's scripts for one workspace."""

    ws: Workspace
    project: Path | None = None  # holds analysis/; defaults to the workspace's parent folder
    run_query: Query = query  # Claude Code, through the Claude Agent SDK
    loop_model: Callable[[Choice], Model] = loop.build_model  # the model loci's own loop calls
    loop_server: Callable[[Workspace], Any] = loop.stdio_server  # the loop's loci MCP server
    settings: Callable[[], Settings] = load_settings  # providers' keys and models
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
        if review not in REVIEW_MODES:
            yield {"type": "error", "message": f"Unknown review mode {review!r}."}
            return
        try:
            choice = resolve(model, self.settings())
        except LociError as e:
            yield _error(e)
            return
        if session_id and loop.is_session(session_id) != (choice.provider.engine == "loop"):
            other = "another provider's model" if choice.provider.engine == "claude" else "Claude"
            yield {
                "type": "error",
                "message": f"This conversation was held with {other}, so "
                f"{choice.provider.label} cannot continue it; start a new conversation.",
            }
            return
        turn = Turn(asyncio.Queue(), review)
        # The answer runs beside this generator, so a review can reach the app while the agent
        # is paused waiting for its answer.
        task = asyncio.create_task(self._pump(message, choice, session_id, turn))
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
        self,
        *,
        model: str,
        session_id: str | None,
        turn: Turn | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ClaudeAgentOptions:
        """Claude Code's options for one message; `env` picks how it reaches Claude."""
        turn = turn or Turn(asyncio.Queue(), DEFAULT_REVIEW)
        self.scripts.mkdir(parents=True, exist_ok=True)
        server = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "loci.server"],
            "env": {ROOT_ENV: str(self.ws.root)},
        }

        async def can_use(
            name: str, arguments: dict[str, Any], context: ToolPermissionContext
        ) -> Permission:
            return await self.permit(turn, name, arguments, context.tool_use_id)

        async def validated(
            hook_input: Any, tool_use_id: str | None, context: Any
        ) -> dict[str, Any]:
            return await self.check(turn, hook_input, tool_use_id)

        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT.format(scripts=self.scripts),
            mcp_servers={"loci": server},  # type: ignore[dict-item]
            strict_mcp_config=True,
            tools=FILE_TOOLS,
            # Nothing is allowed up front: every call goes through permit, which decides.
            allowed_tools=[],
            can_use_tool=can_use,
            setting_sources=[],
            hooks={
                "PreToolUse": [HookMatcher(matcher=GUARDED_TOOLS, hooks=[self._guard])],  # type: ignore[list-item]
                "PostToolUse": [HookMatcher(matcher=VALIDATED_TOOLS, hooks=[validated])],  # type: ignore[list-item]
            },
            model=model,
            resume=session_id,
            max_turns=MAX_TURNS,
            cwd=str(self.folder),
            env=dict(env or {}),
        )

    async def _pump(self, message: str, choice: Choice, session_id: str | None, turn: Turn) -> None:
        """Answer with the engine that runs the model, then end the turn's stream."""
        try:
            if choice.provider.engine == "claude":
                await self._claude(message, choice, session_id, turn)
            else:
                await loop.answer(self, turn, message, choice, session_id)
        except Exception as e:  # report it in the conversation rather than dropping the stream
            await turn.events.put(_error(e))
        finally:
            await turn.events.put(None)

    async def _claude(
        self, message: str, choice: Choice, session_id: str | None, turn: Turn
    ) -> None:
        """Run Claude Code and put its events, then the checks they set off, on the queue."""
        options = self.options(model=choice.model, session_id=session_id, turn=turn, env=choice.env)
        async for item in self.run_query(prompt=message, options=options):
            for event in self._events(item):
                await turn.events.put(event)
            while turn.checks:
                await turn.events.put(turn.checks.pop(0))

    async def permit(
        self, turn: Turn, name: str, arguments: dict[str, Any], tool_use_id: str | None
    ) -> Permission:
        """Whether a tool call may go ahead, asking the scientist where that is their call."""
        if name == RUN_TOOL:
            return await self._permit_run(turn, arguments, tool_use_id)
        if name.startswith(TOOL_PREFIX):
            return PermissionResultAllow()
        if name not in FILE_TOOLS:
            return PermissionResultDeny(message=f"{name} is not available here.")
        file = self.script(arguments.get("file_path"))
        if file is None:
            return PermissionResultDeny(message=self._outside())
        if name == "Read" or not file.exists():
            return PermissionResultAllow()  # reading a script, or writing a new one
        before = file.read_text()
        after = proposed(name, before, arguments)
        if after is None or after == before:
            return PermissionResultAllow()  # nothing to review; the tool reports any error itself
        proposal = {"kind": "edit", "path": self.relative(file), "before": before, "after": after}
        return await self._ask(turn, proposal, tool_use_id, "change to " + self.relative(file))

    async def _permit_run(
        self, turn: Turn, arguments: dict[str, Any], tool_use_id: str | None
    ) -> Permission:
        if arguments.get("code") is not None:
            return PermissionResultDeny(
                message=f"Save the script in {self.scripts} with Write and run it with path=, "
                "so the scientist can see and edit it."
            )
        file = self.script(arguments.get("path"))
        if file is None or not file.exists():
            return PermissionResultDeny(message=f"Run a script that exists in {self.scripts}.")
        updated = {**arguments, "path": str(file)}  # absolute, whatever the call gave
        if turn.review == "runs":
            proposal = {
                "kind": "run",
                "path": self.relative(file),
                "code": file.read_text(),
                "inputs": arguments.get("inputs") or {},
                "parameters": arguments.get("parameters"),
            }
            answer = await self._ask(turn, proposal, tool_use_id, "run of " + self.relative(file))
            if isinstance(answer, PermissionResultDeny):
                return answer
        return PermissionResultAllow(updated_input=updated)

    async def _ask(
        self, turn: Turn, proposal: Event, tool_use_id: str | None, what: str
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
        if self.script(arguments.get("file_path")) is not None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": self._outside(),
            }
        }

    async def check(self, turn: Turn, hook_input: Any, tool_use_id: str | None) -> dict[str, Any]:
        """The validation hook, in process: validate new data_refs and tell the app."""
        event = dict(hook_input)
        output = await anyio.to_thread.run_sync(lambda: hooks.respond(event, self.ws, self.mode))
        if not output:
            return {}
        for line in hook_note(output).splitlines():
            if line.startswith("loci validated "):
                ref, _, rest = line.removeprefix("loci validated ").partition(" automatically: ")
                status = rest.split(" ", 1)[0]
                turn.checks.append(
                    {"type": "check", "tool_use_id": tool_use_id, "data_ref": ref, "status": status}
                )
        return output

    def script(self, path: object) -> Path | None:
        """The file a path names if it is a .py file under analysis/ (relative to the project)."""
        if not isinstance(path, str) or not path:
            return None
        file = (self.folder / Path(path).expanduser()).resolve()
        if file.suffix != ".py" or not file.is_relative_to(self.scripts):
            return None
        return file

    def relative(self, file: Path) -> str:
        return str(file.relative_to(self.folder))

    def shown(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A tool call's arguments as the app shows them: script paths relative to the project."""
        shown = dict(arguments)
        for key in ("file_path", "path"):
            file = self.script(shown.get(key))
            if file is not None:
                shown[key] = self.relative(file)
        return shown

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
                            "input": self.shown(block.input),
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


def _error(e: BaseException) -> Event:
    """A failure as an event: what went wrong, and for loci's errors their hint, apart."""
    while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
        e = e.exceptions[0]  # task groups (the MCP client's) wrap what failed inside them
    if isinstance(e, LociError):
        return {"type": "error", "message": e.message, "hint": e.hint}
    return {"type": "error", "message": f"{type(e).__name__}: {e}"}


def _text(content: str | list[dict[str, Any]] | None) -> str:
    """A tool result's text, shortened; images and other blocks are left out."""
    if content is None:
        return ""
    if isinstance(content, str):
        return clip(content)
    return clip("\n".join(str(b.get("text", "")) for b in content if b.get("type") == "text"))
