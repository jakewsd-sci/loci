"""What both agent engines share: a turn's events, the instructions, tool names and script edits.

The Claude Code engine (agent.py) and loci's own loop (loop.py) run the same conversation rules:
the same instructions, the same tools under the same names, the same reviews and checks, and the
same events for the app.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

Event = dict[str, Any]

REVIEW_MODES = ("changes", "runs")  # what waits for the scientist: script changes, or runs too
DEFAULT_REVIEW = "changes"
MAX_TURNS = 40
TOOL_PREFIX = "mcp__loci__"  # how Claude Code names the loci MCP server's tools
RUN_TOOL = f"{TOOL_PREFIX}run"
VALIDATED = ("load", "stac_search", "run")  # tools whose new data_refs are validated at once
FILE_TOOLS = ["Read", "Write", "Edit"]
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


@dataclass
class Turn:
    """One message being answered: where its events go, and what it is waiting on."""

    events: "asyncio.Queue[Event | None]"
    review: str
    checks: list[Event] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)


def proposed(name: str, before: str, arguments: dict[str, Any]) -> str | None:
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


def hook_note(output: dict[str, Any] | None) -> str:
    """What the validation hook tells the agent: its reason when it blocks, else its context."""
    if not output:
        return ""
    context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
    return str(output.get("reason") or context)


def clip(text: str, limit: int = 4000) -> str:
    """A tool result's text as the app shows it, shortened."""
    return text[:limit]
