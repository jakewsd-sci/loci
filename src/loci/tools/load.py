"""The `load` tool: read a raster or vector source into the workspace and describe it."""

from pathlib import Path

from loci.errors import LoadError
from loci.io import detect_format, is_remote, read
from loci.models import Kind, ReadOptions, RefSummary
from loci.tools.common import describe
from loci.workspace import Workspace

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
) -> RefSummary:
    """Read a dataset into the workspace; return its new data_ref and a compact summary.

    Args:
        uri: Local path, or an http(s)://, s3:// or /vsi... URL.
        kind: "raster" or "vector"; needed only for suffixes loci does not recognise.
        layer: Layer to read from a multi-layer vector file.
        x_col, y_col: Coordinate columns of a CSV or plain Parquet table (detected if omitted).
        crs: CRS for a source that declares none. Never reprojects; a conflict is an error.

    Rasters are opened lazily: the summary describes the grid and bands, not pixel values.
    Vectors are read in full and summarised completely.
    """
    location = _resolve(uri)
    result = read(
        location,
        detect_format(location, kind),
        ReadOptions(layer=layer, x_col=x_col, y_col=y_col, crs=crs),
    )
    record = ws.register(result, location)
    notes = [RASTER_LOAD_NOTE] if result.kind == "raster" else []
    return describe(record, result.data, notes=notes)


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
