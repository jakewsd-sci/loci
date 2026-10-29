"""Run the benchmark: five geospatial questions with exactly known answers, each with a trap.

    uv run python benchmarks/run.py                       # reference solutions, loci tools only
    uv run python benchmarks/run.py --agent               # Claude Code answers (costs usage)
    uv run python benchmarks/run.py --agent --task steep-terrain --repeat 3

Every attempt gets a folder under benchmarks/results/<time>-<mode>/ with its data, its loci
workspace (so lineage works on every answer) and, for agent runs, the session transcript. Scores
go to results.json in that folder and to the terminal (D-041).
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make `benchmarks` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks import agent
from benchmarks.tasks import TASKS, TASKS_BY_ID, Task
from loci.workspace import Workspace

RESULTS = Path(__file__).resolve().parent / "results"


@dataclass
class Score:
    task: str
    attempt: int
    mode: str
    expected: float
    tolerance: float
    value: float | None = None
    correct: bool = False
    caveats: bool | None = None  # agent runs: did the reply mention the trap?
    flags: list[str] = field(default_factory=list)  # reference runs: what loci reported
    turns: int | None = None
    cost_usd: float | None = None
    seconds: float | None = None
    tool_calls: list[str] = field(default_factory=list)
    hook_validations: int | None = None
    folder: str = ""
    error: str | None = None


def attempt(task: Task, folder: Path, *, number: int, use_agent: bool) -> Score:
    """Set the task up in `folder` and answer it, by reference solution or by Claude Code."""
    folder.mkdir(parents=True)
    ws = Workspace(folder / ".loci")
    score = Score(
        task=task.id,
        attempt=number,
        mode="agent" if use_agent else "reference",
        expected=task.answer,
        tolerance=task.tolerance,
        folder=str(folder),
    )
    try:
        names = task.setup(folder, ws)
        if use_agent:
            session = agent.ask(task.question(names), ws.root)
            score.value = session.answer
            score.caveats = all(re.search(c, session.text, re.IGNORECASE) for c in task.caveats)
            score.turns, score.cost_usd = session.turns, session.cost_usd
            score.seconds, score.tool_calls = session.seconds, session.tool_calls
            score.hook_validations = session.hook_validations
            if session.is_error:
                score.error = "The session ended with an error."
        else:
            reference = task.reference(ws, names)
            score.value, score.flags = reference.value, reference.flags
    except Exception as e:  # one broken attempt must not stop the others
        score.error = f"{type(e).__name__}: {e}"
    score.correct = task.correct(score.value)
    return score


def table(scores: list[Score]) -> str:
    """The scores as a plain-text table with a summary line."""
    lines = [
        f"{'task':22} {'expected':>9} {'answer':>9}  correct  caveats {'turns':>5} {'cost':>7} "
        f"{'time':>6}"
    ]
    for s in scores:
        value = "-" if s.value is None else f"{s.value:.4g}"
        caveats = "-" if s.caveats is None else ("yes" if s.caveats else "no")
        cost = "-" if s.cost_usd is None else f"${s.cost_usd:.2f}"
        seconds = "-" if s.seconds is None else f"{s.seconds:.0f} s"
        lines.append(
            f"{s.task:22} {s.expected:>9.4g} {value:>9}  {'yes' if s.correct else 'no':7}  "
            f"{caveats:7} {s.turns if s.turns is not None else '-':>5} {cost:>7} {seconds:>6}"
        )
        if s.error:
            lines.append(f"  error: {s.error.splitlines()[0]}")
    correct = sum(s.correct for s in scores)
    summary = f"{correct}/{len(scores)} correct"
    if any(s.caveats is not None for s in scores):
        summary += f"; {sum(bool(s.caveats) for s in scores)}/{len(scores)} reported the trap"
    if any(s.cost_usd for s in scores):
        summary += f"; ${sum(s.cost_usd or 0 for s in scores):.2f} in total"
    return "\n".join([*lines, summary])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--agent", action="store_true", help="answer with Claude Code sessions")
    parser.add_argument("--task", action="append", choices=sorted(TASKS_BY_ID), help="repeatable")
    parser.add_argument("--repeat", type=int, default=1, help="attempts per task")
    parser.add_argument("--results", type=Path, default=RESULTS, help="where results go")
    args = parser.parse_args(argv)

    tasks = [TASKS_BY_ID[t] for t in args.task] if args.task else TASKS
    mode = "agent" if args.agent else "reference"
    out = args.results / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{mode}"
    scores = []
    for task in tasks:
        for number in range(1, args.repeat + 1):
            folder = out / (task.id if args.repeat == 1 else f"{task.id}-{number}")
            scores.append(attempt(task, folder, number=number, use_agent=args.agent))
            verdict = "correct" if scores[-1].correct else "wrong"
            print(f"{task.id} #{number}: {verdict}", flush=True)
    (out / "results.json").write_text(
        json.dumps([asdict(s) for s in scores], indent=2), encoding="utf-8"
    )
    print(f"\n{table(scores)}\n\nResults: {out / 'results.json'}")
    return 0 if args.agent or all(s.correct for s in scores) else 1


if __name__ == "__main__":
    sys.exit(main())
