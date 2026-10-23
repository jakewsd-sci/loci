"""The benchmark: every reference solution gets its known answer, through loci's tools."""

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks import run
from benchmarks.agent import read_session
from benchmarks.tasks import TASKS, Task
from loci.tools.lineage import lineage
from loci.workspace import Workspace

# What loci reports that points at each task's trap; a careful agent should notice it.
SIGNALS = {
    "cloudy-ndvi": "different native resolutions",
    "catchment-elevation": "fail crs_consistent",
    "forest-area": "is geographic, in degrees",
    "ndvi-at-sites": "warn extents_overlap",
    "steep-terrain": "is geographic, in degrees",
}


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_reference_solutions_get_the_known_answers(task: Task, tmp_path: Path) -> None:
    ws = Workspace(tmp_path / ".loci")

    reference = task.reference(ws, task.setup(tmp_path, ws))

    assert task.correct(reference.value), f"{reference.value} is not {task.answer}"
    assert any(SIGNALS[task.id] in flag for flag in reference.flags)
    traced = lineage(ws, str(reference.answer_file))  # the answer itself has provenance
    assert traced.steps[0].produced_by == "run" and traced.original_sources


def test_tasks_are_distinct_and_their_prompts_complete(tmp_path: Path) -> None:
    assert len({t.id for t in TASKS}) == len(TASKS) == 5
    for task in TASKS:
        ws = Workspace(tmp_path / task.id / ".loci")
        question = task.question(task.setup(tmp_path / task.id, ws))
        assert "{" not in question


def test_the_runner_scores_and_records_reference_runs(tmp_path: Path) -> None:
    code = run.main(["--task", "forest-area", "--results", str(tmp_path)])

    (results,) = tmp_path.glob("*-reference/results.json")
    (score,) = json.loads(results.read_text())
    assert code == 0
    assert score["correct"] and score["task"] == "forest-area" and score["error"] is None


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, **fields}


def test_sessions_are_read_from_the_event_stream() -> None:
    events = [
        _event("assistant", message={"content": [{"type": "tool_use", "name": "mcp__loci__load"}]}),
        _event("system", subtype="hook_response", hook_name="PostToolUse:mcp__loci__load",
               stdout='{"hookSpecificOutput": {"additionalContext": "loci validated raster-1"}}'),
        _event("user", message={"content": [{"type": "tool_result", "is_error": True}]}),
        _event("result", result="Done.\n**ANSWER: `59.6`**", num_turns=4,
               total_cost_usd=0.12, duration_ms=81_000, is_error=False),
    ]  # fmt: skip

    session = read_session(events)

    assert session.answer == 59.6
    assert (session.turns, session.cost_usd, session.seconds) == (4, 0.12, 81.0)
    assert session.tool_calls == ["mcp__loci__load"]
    assert (session.tool_errors, session.hook_validations, session.is_error) == (1, 1, False)


@pytest.mark.parametrize(
    ("text", "answer"),
    [("ANSWER: 0.5477", 0.5477), ("ANSWER: 1e-3\nANSWER: 2", 2.0), ("no number here", None)],
)
def test_the_last_answer_line_counts(text: str, answer: float | None) -> None:
    assert read_session([_event("result", result=text)]).answer == answer
