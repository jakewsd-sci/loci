"""The workspace: a directory of data_ref records, and the data opened from them.

Each data_ref is one JSON record in <root>/refs/. The record says exactly how its data was read,
so any process (the MCP server, a `run` subprocess, a hook) can reopen it. Opened data is cached in
memory per process. The default root is ./.loci, or $LOCI_WORKSPACE if set.
"""

import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from loci.errors import UnknownRefError
from loci.io import Data, Format, ReadResult, is_remote, read
from loci.models import CrsSource, Kind, ReadOptions

ROOT_ENV = "LOCI_WORKSPACE"
DEFAULT_ROOT = ".loci"
REF_PATTERN = re.compile(r"(raster|vector)-[0-9a-f]{8}")


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str
    format: str
    options: ReadOptions


class Fingerprint(BaseModel):
    """Size and modification time of a local source when it was loaded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: int
    mtime_ns: int


class RefRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_ref: str
    kind: Kind
    source: SourceSpec
    created_at: datetime
    session_id: str
    fingerprint: Fingerprint | None = None
    crs_source: CrsSource | None = None
    load_warnings: list[str] = Field(default_factory=list)


class Workspace:
    def __init__(self, root: str | Path, *, session_id: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.refs_dir = self.root / "refs"
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or _new_session_id()
        self._cache: dict[str, Data] = {}

    @classmethod
    def from_env(cls) -> "Workspace":
        return cls(os.environ.get(ROOT_ENV, DEFAULT_ROOT))

    def register(self, result: ReadResult, uri: str) -> RefRecord:
        """Record a freshly read dataset under a new data_ref and keep it open."""
        record = RefRecord(
            data_ref=self._new_ref(result.kind),
            kind=result.kind,
            source=SourceSpec(uri=uri, format=result.format, options=result.options),
            created_at=datetime.now(UTC),
            session_id=self.session_id,
            fingerprint=fingerprint(uri),
            crs_source=result.crs_source,
            load_warnings=result.warnings,
        )
        self._write(record)
        self._cache[record.data_ref] = result.data
        return record

    def record(self, data_ref: str) -> RefRecord:
        path = self._path(data_ref)
        if not path.exists():
            raise UnknownRefError(f"No data_ref {data_ref!r} in this workspace.", hint=self._hint())
        return RefRecord.model_validate_json(path.read_text())

    def open(self, data_ref: str) -> Data:
        """The data behind a data_ref, reopened from its record if this process has not got it."""
        self._path(data_ref)  # validate before touching the cache
        if data_ref not in self._cache:
            record = self.record(data_ref)
            fmt = Format(record.kind, record.source.format)
            self._cache[data_ref] = read(record.source.uri, fmt, record.source.options).data
        return self._cache[data_ref]

    def records(self) -> list[RefRecord]:
        """All records, most recent first."""
        records = [
            RefRecord.model_validate_json(p.read_text()) for p in self.refs_dir.glob("*.json")
        ]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def source_changed(self, record: RefRecord) -> bool:
        """True if a local source's size or modification time differs from when it was loaded."""
        if record.fingerprint is None:
            return False
        return fingerprint(record.source.uri) != record.fingerprint

    def _path(self, data_ref: str) -> Path:
        # Refs arrive from the model; validate them before they become part of a file path.
        if not REF_PATTERN.fullmatch(data_ref):
            raise UnknownRefError(
                f"{data_ref!r} is not a data_ref.",
                hint="data_refs look like 'raster-1a2b3c4d' or 'vector-1a2b3c4d', as returned "
                "by load.",
            )
        return self.refs_dir / f"{data_ref}.json"

    def _new_ref(self, kind: Kind) -> str:
        while True:
            ref = f"{kind}-{secrets.token_hex(4)}"
            if not self._path(ref).exists():
                return ref

    def _write(self, record: RefRecord) -> None:
        final = self._path(record.data_ref)
        temporary = final.with_name(f".{final.name}.tmp")
        temporary.write_text(record.model_dump_json(indent=2))
        os.replace(temporary, final)

    def _hint(self, limit: int = 5) -> str:
        recent = [r.data_ref for r in self.records()[:limit]]
        if not recent:
            return "The workspace has no data_refs yet; call load first."
        return f"Most recent data_refs: {', '.join(recent)}."


def fingerprint(uri: str) -> Fingerprint | None:
    """Fingerprint of a local file; None for remote sources or missing files."""
    if is_remote(uri):
        return None
    try:
        stat = Path(uri).stat()
    except OSError:
        return None
    return Fingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _new_session_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
