"""Export: write a data_ref to a file as it is held, with a provenance sidecar beside it.

The app's way of taking data out of loci (D-050). The file's sidecar names the data_ref it came
from, so `lineage` traces an exported file back to its original sources like a file written by
run. Not an MCP tool: agents save what they make with loci.script.save in a run.
"""

import os
from pathlib import Path

import geopandas as gpd
import xarray as xr

from loci import provenance
from loci.errors import LociError
from loci.io.write import RASTER_FORMATS, VECTOR_FORMATS, write_raster, write_vector
from loci.models import ExportResult, SourceInput
from loci.workspace import Workspace


def export(
    ws: Workspace, data_ref: str, path: str, *, format: str | None = None, step: int | None = None
) -> ExportResult:
    """Write a data_ref to `path` in `format` (by default, from the suffix).

    Rasters go to GeoTIFF, one time step of a time stack at a time (`step`); vectors to
    GeoPackage, GeoJSON or CSV. Nothing is reprojected or resampled. A file already at `path`
    is replaced.
    """
    record = ws.record(data_ref)
    target = Path(path).expanduser().resolve()
    formats = RASTER_FORMATS if record.kind == "raster" else VECTOR_FORMATS
    chosen = format or next((f for f, suffix in formats.items() if suffix == target.suffix), None)
    if chosen not in formats:
        raise LociError(
            f"A {record.kind} cannot be exported as {chosen or target.suffix or 'that'}.",
            hint=f"Use one of {', '.join(f'{f} ({s})' for f, s in formats.items())}.",
        )
    data = ws.open(data_ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.exporting{target.suffix}")
    try:
        if isinstance(data, xr.Dataset):
            notes = write_raster(data, temporary, step=step)
        else:
            assert isinstance(data, gpd.GeoDataFrame)
            notes = write_vector(data, temporary, chosen, name=target.stem)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    call = provenance.tool_call("export", {"data_ref": data_ref, "format": chosen, "step": step})
    sidecar = provenance.new_record(
        produced_by="export",
        call=call,
        inputs=[SourceInput(data_ref=data_ref)],
        parameters={"format": chosen, "step": step},
        path=str(target),
        sha256=provenance.sha256_file(target),
    )
    provenance.write(ws, sidecar)
    return ExportResult(
        data_ref=data_ref,
        path=str(target),
        format=chosen,
        bytes=target.stat().st_size,
        notes=notes,
    )
