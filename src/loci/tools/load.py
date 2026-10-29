"""The `load` tool: read a raster or vector source into the workspace and describe it."""

from pathlib import Path
from typing import Any

from loci import provenance
from loci.errors import LoadError
from loci.io import STAC_ITEMS, STAC_RASTER, ReadResult, detect_format, is_remote, read
from loci.io.stac import read_items
from loci.models import Kind, ReadOptions, RefSummary, SourceInput
from loci.tools.common import describe
from loci.workspace import REF_PATTERN, RefRecord, Workspace

RASTER_LOAD_NOTE = (
    "Pixel statistics are not computed at load; call inspect for per-band statistics."
)


def load(
    ws: Workspace,
    uri: str,
    *,
    kind: Kind | None = None,
    layer: str | None = None,
    x_col: str | None = None,
    y_col: str | None = None,
    crs: str | None = None,
    bands: list[str] | None = None,
    resolution: float | None = None,
    bbox: list[float] | None = None,
    output_crs: str | None = None,
    resampling: str | None = None,
    drop_offset: bool | None = None,
    select: dict[str, str | float | list[str]] | None = None,
) -> RefSummary:
    """Read a dataset into the workspace; return its new data_ref and a compact summary.

    Args:
        uri: Local path, an http(s)://, s3:// or /vsi... URL, or the data_ref of a stac_search
            result (to load the pixels of its items). NetCDF files (.nc) and Zarr stores
            (.zarr, local or https://) are read as rasters, one variable per band.
        kind: "raster" or "vector"; needed only for suffixes loci does not recognise.
        layer: Layer to read from a multi-layer vector file.
        x_col, y_col: Coordinate columns of a CSV or plain Parquet table (detected if omitted).
        crs: CRS for a file that declares none. Never reprojects; a conflict is an error.
        bands: STAC: asset names to load, e.g. ["red", "nir"]. NetCDF and Zarr: the
            variables to read, e.g. ["t2m"]; they must share their dimensions.
        resolution: STAC only. Pixel size in the output CRS's units; needed when bands differ
            in native resolution or items are reprojected.
        bbox: [west, south, east, north] in degrees. STAC: defaults to the search area.
            NetCDF and Zarr on a longitude/latitude grid: the area to read.
        output_crs: STAC only. CRS of the output grid; items in other CRSs are reprojected.
        resampling: STAC only. Method used when resampling or reprojecting (default nearest).
        drop_offset: STAC only. Whether to leave the items' declared offsets out of physical
            values. By default loci decides from the items: left out when every item says its
            stored values already include it (earthsearch:boa_offset_applied), applied when none
            does, and an error when only some do. True or False decides instead. The decision
            is recorded and noted.
        select: NetCDF and Zarr only. A value along each dimension other than x, y and time,
            such as {"level": 850}, and [start, end] for a range, such as
            {"time": ["2020-01-01", "2020-12-31"]}. Needed for dimensions with several values
            and for time axes longer than 10,000 steps.

    Rasters are opened lazily: the summary describes the grid and bands, not pixel values.
    Vectors are read in full and summarised completely.
    """
    options = ReadOptions(
        layer=layer,
        x_col=x_col,
        y_col=y_col,
        crs=crs,
        bands=bands,
        resolution=resolution,
        bbox=tuple(bbox) if bbox is not None else None,  # type: ignore[arg-type]
        output_crs=output_crs,
        resampling=resampling,
        drop_offset=drop_offset,
        select=select,
    )
    call = provenance.tool_call(
        "load",
        {"uri": uri, "kind": kind, **options.model_dump(mode="json", exclude_none=True)},
    )
    if REF_PATTERN.fullmatch(uri):
        return _load_items(ws, uri, options, kind, call)
    location = _resolve(uri)
    result = read(location, detect_format(location, kind), options)
    record = ws.register(result, location)
    _record(ws, record, result, call, [SourceInput(uri=location)])
    notes = [RASTER_LOAD_NOTE] if result.kind == "raster" else []
    return describe(record, result.data, notes=notes)


def _load_items(
    ws: Workspace, data_ref: str, options: ReadOptions, kind: Kind | None, call: dict[str, Any]
) -> RefSummary:
    """Load the pixels of a stac_search result as a new raster data_ref."""
    record = ws.record(data_ref)
    if record.source.format != STAC_ITEMS.name:
        raise LoadError(
            f"{data_ref} is not a stac_search result, so there is nothing to load from it.",
            hint="load takes a file path or URL, or the data_ref returned by stac_search.",
        )
    if kind not in (None, "raster"):
        raise LoadError("Pixels loaded from STAC items are rasters; omit kind or pass 'raster'.")
    if options.drop_offset is None:  # not chosen: decide from the items (D-046)
        options = options.model_copy(update={"drop_offset": "auto"})
    result = read(record.source.uri, STAC_RASTER, options)
    loaded = ws.register(result, record.source.uri)
    items = [item.id for item in read_items(record.source.uri)[0]]
    source = SourceInput(data_ref=data_ref, uri=record.source.uri, stac_items=items)
    _record(ws, loaded, result, call, [source])
    return describe(loaded, result.data, notes=[RASTER_LOAD_NOTE])


def _record(
    ws: Workspace,
    record: RefRecord,
    result: ReadResult,
    call: dict[str, Any],
    inputs: list[SourceInput],
) -> None:
    """Provenance for a load: the call, its resolved read options and what was read."""
    parameters = result.options.model_dump(mode="json", exclude_none=True)
    provenance.write(
        ws,
        provenance.new_record(
            produced_by="load",
            data_ref=record.data_ref,
            call=call,
            inputs=inputs,
            parameters=parameters,
        ),
    )


def _resolve(uri: str) -> str:
    """Absolute path for local files (so records work from any directory); URLs unchanged."""
    if is_remote(uri):
        return uri
    path = Path(uri).expanduser()
    if not path.exists():
        raise LoadError(
            f"No such file: {uri!r}.",
            hint=f"Relative paths are resolved from {Path.cwd()}.",
        )
    return str(path.resolve())
