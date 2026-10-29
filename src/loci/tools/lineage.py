"""The `lineage` tool: how an output was made, back to its original sources."""

from collections import deque
from pathlib import Path

from loci import provenance
from loci.errors import LociError, UnknownRefError
from loci.models import LineageResult, LineageStep, Provenance
from loci.workspace import REF_PATTERN, RefRecord, Workspace

MAX_STEPS = 100
MAX_ITEMS_LISTED = 10


def lineage(ws: Workspace, target: str) -> LineageResult:
    """Trace a data_ref, or a file written by run, back to the files, URLs and STAC items it
    came from.

    Each step names what produced it (load, stac_search or run), its parent data_refs, what it
    read from outside the workspace, the number of STAC items it used and its latest validation
    status. `original_sources` gathers everything read from outside the workspace at any step.
    """
    warnings: list[str] = []
    notes: list[str] = []
    if REF_PATTERN.fullmatch(target):
        ws.record(target)  # unknown data_refs fail here
        own, file, record_path = provenance.read(ws, target), None, provenance.path_for(ws, target)
        first = _ref_step(ws, target, warnings, notes)
        parents = first.inputs
    else:
        own, file, record_path = _file_record(target)
        first = _step(own, data_ref=None, file=file)
        parents = first.inputs

    steps = [first]
    seen = {target}
    queue = deque(parents)
    while queue and len(steps) < MAX_STEPS:
        ref = queue.popleft()
        if ref in seen:
            continue
        seen.add(ref)
        try:
            step = _ref_step(ws, ref, warnings, notes)
        except UnknownRefError:
            warnings.append(f"Ancestor {ref} is no longer in the workspace; lineage stops there.")
            continue
        steps.append(step)
        queue.extend(step.inputs)
    if queue:
        warnings.append(f"Lineage is longer than {MAX_STEPS} steps; the rest is left out.")

    items = _stac_items(ws, steps, own)
    return LineageResult(
        data_ref=target if file is None else None,
        file=file,
        provenance=_compact(own) if own else None,
        record_path=str(record_path) if record_path and record_path.exists() else None,
        steps=steps,
        original_sources=list(dict.fromkeys(s for step in steps for s in step.sources)),
        stac_item_count=len(items),
        stac_items=items[:MAX_ITEMS_LISTED],
        warnings=warnings,
        notes=notes,
    )


def _file_record(target: str) -> tuple[Provenance, str, Path]:
    path = Path(target).expanduser().resolve()
    sidecar = Path(f"{path}{provenance.SIDECAR_SUFFIX}")
    if not sidecar.exists():
        raise LociError(
            f"{target!r} is neither a data_ref nor a file with a provenance sidecar.",
            hint="Pass a data_ref, or the path of a file written by run.",
        )
    return Provenance.model_validate_json(sidecar.read_text(encoding="utf-8")), str(path), sidecar


def _ref_step(ws: Workspace, ref: str, warnings: list[str], notes: list[str]) -> LineageStep:
    record = ws.record(ref)
    if ws.source_changed(record):
        warnings.append(f"The source of {ref} has changed since it was loaded.")
    found = provenance.read(ws, ref)
    if found is None:
        notes.append(
            f"{ref} has no provenance record (it predates provenance); shown from its "
            "workspace record."
        )
        return _legacy_step(record)
    return _step(found, data_ref=ref, file=None)


def _step(record: Provenance, *, data_ref: str | None, file: str | None) -> LineageStep:
    return LineageStep(
        data_ref=data_ref,
        file=file,
        produced_by=record.produced_by,
        created_at=record.created_at,
        description=_describe(record),
        inputs=[i.data_ref for i in record.inputs if i.data_ref],
        sources=[i.uri for i in record.inputs if i.uri and not i.data_ref],
        stac_items=sum(len(i.stac_items) for i in record.inputs),
        validation=record.validation.status if record.validation else None,
    )


def _legacy_step(record: RefRecord) -> LineageStep:
    return LineageStep(
        data_ref=record.data_ref,
        produced_by=None,
        created_at=record.created_at.isoformat(timespec="seconds"),
        description=f"Registered from {record.source.uri}.",
        sources=[record.source.uri],
    )


def _describe(record: Provenance) -> str:
    parameters = record.parameters
    if record.produced_by == "stac_search":
        count = sum(len(i.stac_items) for i in record.inputs)
        return (
            f"stac_search of {parameters.get('collection')} in {parameters.get('catalog')}: "
            f"{count} items from {parameters.get('start')} to {parameters.get('end')}."
        )
    if record.produced_by == "run":
        names = [i.name or i.data_ref for i in record.inputs]
        code = (record.code_sha256 or "")[:12]
        return f"run {record.run_id} of a script (sha256 {code}…) with inputs {names}."
    parent = next((i for i in record.inputs if i.data_ref), None)
    if parent is not None:
        return (
            f"load of bands {parameters.get('bands')} from {len(parent.stac_items)} STAC items "
            f"found by {parent.data_ref}{_chosen(record)}{_offset_left_out(record)}."
        )
    source = next((i.uri for i in record.inputs if i.uri), None)
    return f"load of {source}{_chosen(record)}."


def _chosen(record: Provenance) -> str:
    """Options the caller set explicitly, such as crs= or drop_offset=, which change the data."""
    arguments = record.call.get("arguments")
    if not isinstance(arguments, dict):
        return ""
    chosen = [f"{k}={v!r}" for k, v in arguments.items() if k not in {"uri", "kind", "bands"}]
    return f", with {', '.join(chosen)}" if chosen else ""


def _offset_left_out(record: Provenance) -> str:
    """Says so when loci left a declared offset out on its own (drop_offset not given)."""
    arguments = record.call.get("arguments")
    chosen = isinstance(arguments, dict) and "drop_offset" in arguments
    if chosen or record.parameters.get("drop_offset") is not True:
        return ""
    return ", leaving out the declared offset that every item marks as already applied"


def _stac_items(ws: Workspace, steps: list[LineageStep], own: Provenance | None) -> list[str]:
    """Every STAC item behind the output, each once, in the order the records list them."""
    items: list[str] = []
    records = [own] + [provenance.read(ws, s.data_ref) for s in steps[1:] if s.data_ref]
    for record in records:
        if record is not None:
            for source in record.inputs:
                items.extend(source.stac_items)
    return list(dict.fromkeys(items))


def _compact(record: Provenance) -> Provenance:
    """The record without its validation report, with STAC item lists shortened."""
    inputs = [
        i.model_copy(update={"stac_items": i.stac_items[:MAX_ITEMS_LISTED]}) for i in record.inputs
    ]
    return record.model_copy(update={"validation": None, "inputs": inputs})
