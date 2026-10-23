"""The `load` tool: read a raster or vector source into the workspace and describe it."""

from pathlib import Path

from loci.errors import LoadError
from loci.io import STAC_ITEMS, STAC_RASTER, detect_format, is_remote, read
from loci.models import Kind, ReadOptions, RefSummary
from loci.tools.common import describe
from loci.workspace import REF_PATTERN, Workspace

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
    drop_offset: bool = False,
) -> RefSummary:
    """Read a dataset into the workspace; return its new data_ref and a compact summary.

    Args:
        uri: Local path, an http(s)://, s3:// or /vsi... URL, or the data_ref of a stac_search
            result (to load the pixels of its items).
        kind: "raster" or "vector"; needed only for suffixes loci does not recognise.
        layer: Layer to read from a multi-layer vector file.
        x_col, y_col: Coordinate columns of a CSV or plain Parquet table (detected if omitted).
        crs: CRS for a file that declares none. Never reprojects; a conflict is an error.
        bands: STAC only. Asset names to load, e.g. ["red", "nir"].
        resolution: STAC only. Pixel size in the output CRS's units; needed when bands differ
            in native resolution or items are reprojected.
        bbox: STAC only. [west, south, east, north] in degrees; defaults to the search area.
        output_crs: STAC only. CRS of the output grid; items in other CRSs are reprojected.
        resampling: STAC only. Method used when resampling or reprojecting (default nearest).
        drop_offset: STAC only. Leave the items' declared offsets out of physical values, for
            stored values that already have the offset applied. Recorded and noted; items that
            do not say so are warned about.

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
        drop_offset=True if drop_offset else None,
    )
    if REF_PATTERN.fullmatch(uri):
        return _load_items(ws, uri, options, kind)
    location = _resolve(uri)
    result = read(location, detect_format(location, kind), options)
    record = ws.register(result, location)
    notes = [RASTER_LOAD_NOTE] if result.kind == "raster" else []
    return describe(record, result.data, notes=notes)


def _load_items(
    ws: Workspace, data_ref: str, options: ReadOptions, kind: Kind | None
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
    result = read(record.source.uri, STAC_RASTER, options)
    loaded = ws.register(result, record.source.uri)
    return describe(loaded, result.data, notes=[RASTER_LOAD_NOTE])


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
