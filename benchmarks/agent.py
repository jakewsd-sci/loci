"""Ask Claude Code a question in this project, with only the loci tools, and read what it did.

Each session is `claude -p` in the repository root, so Claude Code starts the loci server from
.mcp.json and runs the validation hook from .claude/settings.json, on the task's own workspace.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NOT_LOCI = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,Agent"
INSTRUCTION = (
    "\n\nUse the loci tools for all data work. End your reply with a line `ANSWER: <number>`, "
    "the number alone, without units."
)
ANSWER_LINE = re.compile(r"ANSWER:\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


@dataclass(frozen=True)
class Session:
    """What a Claude Code session did and said."""

    text: str
    turns: int
    cost_usd: float
    seconds: float
    tool_calls: list[str]
    tool_errors: int
    hook_validations: int
    is_error: bool

    @property
    def answer(self) -> float | None:
        """The number on the last `ANSWER:` line, ignoring markdown around it."""
        found = ANSWER_LINE.findall(self.text.replace("*", "").replace("`", ""))
        return float(found[-1]) if found else None


def ask(question: str, workspace: Path, *, mode: str = "report", timeout: float = 1800) -> Session:
    """Run one session on `workspace`; its event stream is kept beside the workspace."""
    settings = workspace.parent / "agent-settings.json"
    settings.write_text(json.dumps({"env": {"LOCI_VALIDATION_HOOK": mode}}))
    command = [
        *("claude", "-p", question + INSTRUCTION, "--settings", str(settings)),
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
        timeout=timeout,
    )
    (workspace.parent / "session.jsonl").write_text(result.stdout)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not events:
        raise RuntimeError(f"claude gave no output (exit {result.returncode}):\n{result.stderr}")
    return read_session(events)


def read_session(events: list[dict[str, Any]]) -> Session:
    """Summarise a stream-json event list from `claude -p --output-format stream-json`."""
    final: dict[str, Any] = next((e for e in reversed(events) if e.get("type") == "result"), {})
    calls: list[str] = []
    errors = 0
    for event in events:
        content = event.get("message", {}).get("content", [])
        if event.get("type") == "assistant":
            calls += [block["name"] for block in content if block.get("type") == "tool_use"]
        elif event.get("type") == "user" and isinstance(content, list):
            errors += sum(1 for b in content if isinstance(b, dict) and b.get("is_error"))
    validations = sum(
        1
        for e in events
        if e.get("subtype") == "hook_response"
        and str(e.get("hook_name", "")).startswith("PostToolUse:mcp__loci__")
        and "loci validated" in str(e.get("stdout", ""))
    )
    return Session(
        text=str(final.get("result", "")),
        turns=int(final.get("num_turns", 0)),
        cost_usd=float(final.get("total_cost_usd", 0.0)),
        seconds=float(final.get("duration_ms", 0)) / 1000,
        tool_calls=calls,
        tool_errors=errors,
        hook_validations=validations,
        is_error=bool(final.get("is_error", True)),
    )
