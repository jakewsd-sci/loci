"""The project's data files, which the app lists beside the layers, and the files people add.

Every file loci can read in the project folder is listed, so a scientist sees their own data and
puts it on the map with a click (D-050). Hidden folders, the workspace and analysis/ are skipped.
Files dropped on the map are saved in data/, and exports go to exports/, each beside the data
they came from rather than inside the workspace, so they stay the scientist's own files.
"""

import os
import re
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from loci.errors import LociError
from loci.io.formats import SUFFIXES
from loci.models import Kind
from loci.provenance import sha256_file
from loci.web.project import SCRIPTS_DIR
from loci.workspace import Workspace

DATA_DIR = "data"
EXPORTS_DIR = "exports"
SKIPPED = {SCRIPTS_DIR, "node_modules", "__pycache__", "venv", "site-packages"}
MAX_FILES = 500
MAX_DEPTH = 6
# Plain .json is listed only as .geojson: a project's other JSON files are rarely geodata.
LISTED = {suffix: fmt for suffix, fmt in SUFFIXES.items() if suffix != ".json"}
# A shapefile comes as several files; they travel together when dropped on the map.
SIDECARS = {".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".xml"}
NAME = re.compile(r"[^\\/:*?\"<>|\x00-\x1f]+")  # a file name, valid on Windows too


class DataFile(BaseModel):
    """A file in the project that loci can read."""

    path: str  # relative to the project, with /
    kind: Kind
    format: str
    size: int
    modified: str
    data_ref: str | None  # its newest layer, if it is on the map


class DataFiles(BaseModel):
    files: list[DataFile]
    more: bool  # there were more files than are listed


def data_files(project: Path, ws: Workspace) -> DataFiles:
    """The project's readable files, by path, with the layer each is shown as, if any."""
    shown = _layers_by_file(ws)
    files: list[DataFile] = []
    for path in _walk(project):
        if len(files) == MAX_FILES:
            return DataFiles(files=files, more=True)
        stat = path.stat()
        fmt = LISTED[path.suffix.lower()]
        files.append(
            DataFile(
                path=path.relative_to(project).as_posix(),
                kind=fmt.kind,
                format=fmt.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
                data_ref=shown.get(os.path.normcase(str(path.resolve()))),
            )
        )
    return DataFiles(files=files, more=False)


def project_file(project: Path, path: str) -> Path:
    """The file a project-relative path names, if it is inside the project and loci reads it."""
    file = (project / path).resolve()
    if not file.is_relative_to(project.resolve()) or not file.is_file():
        raise LociError(f"No file {path!r} in this project.")
    if file.suffix.lower() not in LISTED:
        raise LociError(
            f"loci cannot read {file.suffix or 'files without a suffix'} files.",
            hint=f"It reads {', '.join(sorted(LISTED))}.",
        )
    return file


class Saved(BaseModel):
    path: Path
    unchanged: bool  # the same bytes were there already, so the file was left as it was


async def save_upload(
    project: Path, name: str, chunks: AsyncIterator[bytes], *, replace: bool = False
) -> Saved:
    """Save a file dropped on the map in data/, without replacing one unless asked. A file
    dropped again unchanged is left alone, so its layer still matches it."""
    suffix = Path(name).suffix.lower()
    if not NAME.fullmatch(name) or name.startswith(".") or Path(name).name != name:
        raise LociError(f"{name!r} is not a plain file name.")
    if suffix not in LISTED and suffix not in SIDECARS:
        raise LociError(
            f"loci cannot read {suffix or 'files without a suffix'} files.",
            hint=f"Add data as {', '.join(sorted(LISTED))} (and a shapefile's companion files).",
        )
    target = project / DATA_DIR / name
    if target.exists() and not replace:
        raise FileExistsError(f"{DATA_DIR}/{name} is already in the project.")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{name}.", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as file:
            async for chunk in chunks:
                file.write(chunk)
        if target.exists() and sha256_file(target) == sha256_file(Path(temporary)):
            Path(temporary).unlink()
            return Saved(path=target, unchanged=True)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Saved(path=target, unchanged=False)


def export_path(project: Path, name: str, suffix: str) -> Path:
    """A new file in exports/ named after the layer, never replacing an earlier export."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(name).stem).strip("-.") or "layer"
    folder = project / EXPORTS_DIR
    candidate = folder / f"{stem}{suffix}"
    count = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{count}{suffix}"
        count += 1
    return candidate


def _walk(project: Path) -> Iterator[Path]:
    """Readable files under the project, in a stable order, skipping what is not data."""
    root_depth = len(project.parts)
    for folder, dirs, names in os.walk(project):
        depth = len(Path(folder).parts) - root_depth
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and d not in SKIPPED and depth < MAX_DEPTH
        )
        for name in sorted(names):
            if Path(name).suffix.lower() in LISTED and not name.startswith("."):
                yield Path(folder) / name


def _layers_by_file(ws: Workspace) -> dict[str, str]:
    """For each file loaded into the workspace, the newest data_ref made from it."""
    shown: dict[str, str] = {}
    for record in reversed(ws.records()):  # oldest first, so the newest wins
        path = Path(record.source.uri)
        if path.is_absolute():
            shown[os.path.normcase(str(path.resolve()))] = record.data_ref
    return shown
