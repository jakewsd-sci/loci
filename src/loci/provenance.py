"""Provenance records: how each output came to be.

Every data_ref gets a record at <workspace>/provenance/<data_ref>.json. Files written by `run` also
get a copy beside them (<file>.prov.json), so the record travels with the file. `lineage` reads
these records to trace an output back to its original sources.
"""

import hashlib
import platform
from datetime import UTC, datetime
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import rasterio

import loci
from loci.models import ProducedBy, Provenance, SourceInput, ValidationReport
from loci.workspace import REF_PATTERN, Workspace

PACKAGES = (
    "numpy",
    "pandas",
    "xarray",
    "rioxarray",
    "rasterio",
    "geopandas",
    "shapely",
    "pyproj",
    "dask",
    "odc-stac",
    "pystac-client",
    "matplotlib",
)
SIDECAR_SUFFIX = ".prov.json"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@cache
def software() -> dict[str, str]:
    """Python, loci (version and a hash of its source), GDAL and key package versions."""
    versions = {
        "python": platform.python_version(),
        "loci": loci.__version__,
        "loci_code_sha256": _loci_code_hash(),
        "gdal": rasterio.__gdal_version__,
    }
    for package in PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            continue
    return versions


def _loci_code_hash() -> str:
    """One hash over loci's own source files, so a record pins the exact engine code."""
    package = Path(loci.__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def tool_call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The call as made, leaving out arguments that were not given."""
    given = {k: v for k, v in arguments.items() if v is not None and v != {} and v != []}
    return {"tool": tool, "arguments": given}


def new_record(
    *,
    produced_by: ProducedBy,
    call: dict[str, Any],
    inputs: list[SourceInput],
    data_ref: str | None = None,
    parameters: dict[str, Any] | None = None,
    **extra: Any,
) -> Provenance:
    return Provenance(
        data_ref=data_ref,
        created_at=now(),
        produced_by=produced_by,
        call=call,
        inputs=inputs,
        parameters=parameters or {},
        software=software(),
        **extra,
    )


def path_for(ws: Workspace, data_ref: str) -> Path:
    if not REF_PATTERN.fullmatch(data_ref):
        raise ValueError(f"{data_ref!r} is not a data_ref")
    return ws.provenance_dir / f"{data_ref}.json"


def write(ws: Workspace, record: Provenance) -> None:
    """Save a data_ref's record (atomically) and, for files, the sidecar beside the file."""
    text = record.model_dump_json(indent=2, exclude_none=True)
    if record.data_ref is not None:
        target = path_for(ws, record.data_ref)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(text)
        temporary.replace(target)
    if record.path is not None:
        Path(record.path + SIDECAR_SUFFIX).write_text(text)


def read(ws: Workspace, data_ref: str) -> Provenance | None:
    path = path_for(ws, data_ref)
    return Provenance.model_validate_json(path.read_text()) if path.exists() else None


def record_validation(ws: Workspace, report: ValidationReport) -> None:
    """Keep the latest validation report on the record of every data_ref it covers."""
    validated_at = now()
    for data_ref in report.data_refs:
        record = read(ws, data_ref)
        if record is not None:
            write(
                ws, record.model_copy(update={"validation": report, "validated_at": validated_at})
            )
