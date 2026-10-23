"""The project's analysis scripts, which the app shows and edits beside the map.

Scripts live in <project>/analysis/ as ordinary .py files under version control, and run reads
them by path, so provenance names the file and the exact version (its hash) that ran.
"""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from loci.errors import LociError

SCRIPTS_DIR = "analysis"


class ScriptFile(BaseModel):
    path: str  # relative to the project, e.g. analysis/ndvi_median.py
    size: int
    modified: str


class ScriptContent(BaseModel):
    path: str
    content: str


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
