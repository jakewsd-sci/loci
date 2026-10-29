"""The project's analysis scripts, which the app shows and edits beside the map.

Scripts live in <project>/analysis/ as ordinary .py files under version control, and run reads
them by path, so provenance names the file and the exact version (its hash) that ran.
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from loci import provenance
from loci.errors import LociError
from loci.models import Provenance
from loci.workspace import Workspace

SCRIPTS_DIR = "analysis"
RUN_ID = re.compile(r"run-[0-9a-f]{8}")


class ScriptFile(BaseModel):
    path: str  # relative to the project, e.g. analysis/ndvi_median.py
    size: int
    modified: str


class ScriptContent(BaseModel):
    path: str
    content: str


class ScriptLink(BaseModel):
    """The script behind an output of run: the project file it ran, and whether that changed."""

    run_id: str
    path: str | None  # e.g. analysis/ndvi.py; None for a script given inline or kept elsewhere
    changed: bool  # the file now differs from the version that ran, or is gone


class RunScript(BaseModel):
    """The exact script a run executed, as kept in its run folder."""

    run_id: str
    content: str
    sha256: str


def scripts(project: Path) -> list[ScriptFile]:
    """Every .py file under analysis/, sorted by path."""
    folder = project / SCRIPTS_DIR
    if not folder.is_dir():
        return []
    return [
        ScriptFile(
            path=str(p.relative_to(project)),
            size=p.stat().st_size,
            modified=datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(timespec="seconds"),
        )
        for p in sorted(folder.rglob("*.py"))
    ]


def read_script(project: Path, path: str) -> ScriptContent:
    file = script_path(project, path)
    if not file.exists():
        raise LociError(f"No script {path!r}.", hint=f"Scripts live in {SCRIPTS_DIR}/.")
    return ScriptContent(path=path, content=file.read_text())


def write_script(project: Path, path: str, content: str) -> ScriptContent:
    file = script_path(project, path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content)
    return ScriptContent(path=path, content=content)


def script_path(project: Path, path: str) -> Path:
    """The file for a project-relative script path; only .py files under analysis/."""
    folder = (project / SCRIPTS_DIR).resolve()
    file = (project / path).resolve()
    if file.suffix != ".py" or not file.is_relative_to(folder):
        raise LociError(
            f"{path!r} is not a script the app can open.",
            hint=f"Scripts are .py files under {SCRIPTS_DIR}/, e.g. {SCRIPTS_DIR}/ndvi.py.",
        )
    return file


def script_link(project: Path, record: Provenance) -> ScriptLink | None:
    """How an output of run links to its script; None for outputs of other tools."""
    if record.produced_by != "run" or record.run_id is None:
        return None
    arguments = record.call.get("arguments")
    given = arguments.get("path") if isinstance(arguments, dict) else None
    file = Path(given).resolve() if isinstance(given, str) else None
    folder = (project / SCRIPTS_DIR).resolve()
    if file is None or file.suffix != ".py" or not file.is_relative_to(folder):
        return ScriptLink(run_id=record.run_id, path=None, changed=False)
    current = provenance.sha256_file(file) if file.exists() else None
    return ScriptLink(
        run_id=record.run_id,
        path=str(file.relative_to(project.resolve())),
        changed=current != record.code_sha256,
    )


def run_script(ws: Workspace, run_id: str) -> RunScript:
    """The script a run executed, from its run folder."""
    file = ws.runs_dir / run_id / "script.py"
    if not RUN_ID.fullmatch(run_id) or not file.exists():
        raise LociError(f"No run {run_id!r} in this workspace.")
    content = file.read_text()
    return RunScript(run_id=run_id, content=content, sha256=provenance.sha256_text(content))


def scripts_fingerprint(project: Path) -> str:
    """Changes whenever a script under analysis/ is added, removed or saved."""
    folder = project / SCRIPTS_DIR
    digest = hashlib.sha256()
    for file in sorted(folder.rglob("*.py")) if folder.is_dir() else []:
        digest.update(f"{file.relative_to(project)}:{file.stat().st_mtime_ns};".encode())
    return digest.hexdigest()[:16]
