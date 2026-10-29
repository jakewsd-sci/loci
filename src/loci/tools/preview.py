"""The `preview` tool: a small PNG of any data_ref, for a vision-capable model to look at."""

import hashlib
import math

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import xarray as xr

from loci.errors import LociError
from loci.io import STAC_RASTER, read
from loci.io.stac import items_frame, read_items
from loci.models import Extent, PreviewResult
from loci.render import COLORMAP, render_raster, render_vector
from loci.summary.common import extent, iso
from loci.summary.raster import valid_mask
from loci.tools.common import overview_source
from loci.workspace import RefRecord, Workspace

DEFAULT_WIDTH = 512
MIN_WIDTH, MAX_WIDTH = 64, 1024
RGB = ("red", "green", "blue")


def preview(
    ws: Workspace,
    data_ref: str,
    *,
    bands: list[str] | None = None,
    time: str | None = None,
    column: str | None = None,
    width: int = DEFAULT_WIDTH,
) -> PreviewResult:
    """Draw a data_ref as a PNG at most `width` pixels wide and return its path.

    Args:
        bands: Raster only. One band (drawn in viridis) or three (as red, green, blue). Defaults
            to true colour when bands named red, green and blue exist, else the first band.
        time: Raster only. Date of the time step to draw; the nearest step is used. Defaults to
            the least cloudy step of a STAC load, else the first.
        column: Vector only. Attribute to colour features by.
        width: Picture width in pixels (64 to 1024).

    Raster values are stretched between their 2nd and 98th percentiles; nodata is transparent.
    Pixels are read at roughly the picture's resolution, from overviews where they exist.
    """
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise LociError(f"width must be between {MIN_WIDTH} and {MAX_WIDTH} pixels.")
    record = ws.record(data_ref)
    data = ws.open(data_ref)
    if isinstance(data, xr.Dataset):
        if column is not None:
            raise LociError("column= applies to vector layers; use bands= for rasters.")
        return _raster_preview(ws, record, data, bands, time, width)
    if bands is not None or time is not None:
        raise LociError("bands= and time= apply to rasters; use column= for vector layers.")
    return _vector_preview(ws, record, data, column, width)


def _raster_preview(
    ws: Workspace,
    record: RefRecord,
    ds: xr.Dataset,
    bands: list[str] | None,
    time: str | None,
    width: int,
) -> PreviewResult:
    chosen, how = _choose_bands([str(n) for n in ds.data_vars], bands)
    notes: list[str] = []
    step = None
    if "time" in ds.dims:
        step, note = _choose_step(record, pd.DatetimeIndex(ds["time"].values), time)
        notes.append(note)
    elif time is not None:
        raise LociError(f"{record.data_ref} has no time dimension, so time= does not apply.")

    source, factor = _pixels(record, ds, width)
    if factor > 1:
        notes.append(f"Drawn at 1/{factor} of the data's resolution.")
    view = source.isel(time=step) if step is not None else source
    arrays = [_physical(view[name]) for name in chosen]
    if any("scale_factor" in view[n].attrs or "add_offset" in view[n].attrs for n in chosen):
        notes.append("Stretch limits are physical values (stored * scale + offset).")

    path = ws.previews_dir / f"{record.data_ref}-{_digest(chosen, step, width)}.png"
    rendered = render_raster(arrays, path)
    warnings = [] if rendered.stretch else ["Nothing to draw: no valid pixels in this view."]
    when = f" on {iso(pd.Timestamp(ds['time'].values[step]))}" if step is not None else ""
    stretch = ""
    if rendered.stretch is not None:
        low, high = rendered.stretch
        stretch = (
            f", stretched from {low:.4g} to {high:.4g}"
            if high > low
            else f"; every valid pixel is {low:.4g}"
        )
    colours = "" if len(chosen) == 3 else f" in {COLORMAP} (dark low, bright high)"
    return PreviewResult(
        data_ref=record.data_ref,
        path=str(path),
        width=rendered.width,
        height=rendered.height,
        shows=f"{how}{when}{colours}{stretch}; nodata is transparent.",
        bands=chosen,
        time=iso(pd.Timestamp(ds["time"].values[step])) if step is not None else None,
        stretch=rendered.stretch,
        extent=extent(ds.rio.bounds(), ds.rio.crs),
        warnings=warnings,
        notes=notes,
    )


def _vector_preview(
    ws: Workspace, record: RefRecord, gdf: gpd.GeoDataFrame, column: str | None, width: int
) -> PreviewResult:
    if column is not None and column not in gdf.columns:
        raise LociError(
            f"No column {column!r}.", hint=f"Columns: {', '.join(map(str, gdf.columns))}."
        )
    geoms = gdf.geometry.to_numpy()
    present = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
    path = ws.previews_dir / f"{record.data_ref}-{_digest([column or ''], None, width)}.png"
    rendered = render_vector(gdf, path, width=width, column=column)
    count = int(present.sum())
    coloured = f", coloured by '{column}' in {COLORMAP}" if column else ""
    return PreviewResult(
        data_ref=record.data_ref,
        path=str(path),
        width=rendered.width,
        height=rendered.height,
        shows=f"{count} features to scale on white{coloured}.",
        column=column,
        extent=(
            extent(shapely.total_bounds(geoms[present]), gdf.crs)
            if count
            else Extent(bounds=(0.0, 0.0, 0.0, 0.0))
        ),
        warnings=[] if count else ["Nothing to draw: the layer has no geometries."],
    )


def _choose_bands(names: list[str], requested: list[str] | None) -> tuple[list[str], str]:
    if requested is not None:
        if len(requested) not in (1, 3):
            raise LociError("Pass one band (drawn in a colormap) or three (drawn as RGB).")
        unknown = [b for b in requested if b not in names]
        if unknown:
            raise LociError(f"Unknown band(s) {unknown}.", hint=f"Bands: {', '.join(names)}.")
        label = "Bands" if len(requested) == 3 else "Band"
        return list(requested), f"{label} {', '.join(requested)}"
    by_lower = {n.lower(): n for n in names}
    if all(c in by_lower for c in RGB):
        rgb = [by_lower[c] for c in RGB]
        return rgb, f"True colour ({', '.join(rgb)})"
    return [names[0]], f"Band {names[0]}"


def _choose_step(
    record: RefRecord, times: pd.DatetimeIndex, requested: str | None
) -> tuple[int, str]:
    """Index of the time step to draw, and a note saying why it was chosen."""
    if requested is not None:
        try:
            target = pd.Timestamp(requested)
        except ValueError as e:
            raise LociError(f"time= must be a date, such as 2024-03-14: {e}") from e
        step = int(np.argmin(np.abs((times - target).total_seconds())))
        return step, f"Showing the step nearest {requested}: {iso(times[step])}."
    clouds = step_cloud_cover(record, times)
    if clouds is not None and clouds.notna().any():
        step = int(clouds.to_numpy().argmin())
        return step, (
            f"Showing {iso(times[step])}, the least cloudy of {len(times)} steps "
            f"({clouds.iloc[step]:.1f}% cloud by item metadata); pass time= for another."
        )
    return 0, f"Showing the first of {len(times)} time steps; pass time= for another."


def step_cloud_cover(record: RefRecord, times: pd.DatetimeIndex) -> pd.Series | None:
    """Mean item cloud cover per time step of a STAC load, from items within 12 hours of it."""
    if record.source.format != STAC_RASTER.name:
        return None
    frame = items_frame(read_items(record.source.uri)[0])
    covers = []
    for t in times:
        near = frame.loc[(frame["datetime"] - t).abs() <= pd.Timedelta(hours=12), "cloud_cover"]
        covers.append(near.mean() if len(near) else np.nan)
    return pd.Series(covers)


def _pixels(record: RefRecord, ds: xr.Dataset, width: int) -> tuple[xr.Dataset, int]:
    """The data at about the picture's resolution, and the decimation factor used."""
    factor = math.ceil(max(ds.sizes["x"], ds.sizes["y"]) / width)
    if factor <= 1:
        return ds, 1
    options = record.source.options
    if record.source.format == STAC_RASTER.name and options.resolution is not None:
        coarser = options.model_copy(update={"resolution": options.resolution * factor})
        coarse = read(record.source.uri, STAC_RASTER, coarser).data
        assert isinstance(coarse, xr.Dataset)
        return coarse, factor
    if "time" not in ds.dims:
        overview = overview_source(record, factor)
        if overview is not None:
            return overview.ds, overview.decimation
    return ds.isel(x=slice(None, None, factor), y=slice(None, None, factor)), factor


def _physical(da: xr.DataArray) -> np.ndarray:
    """Values as float with invalid pixels as NaN, scaled to physical units if declared."""
    values = np.asarray(da.values).astype("float64")
    nodata = da.rio.nodata
    values[~valid_mask(values, None if nodata is None else float(nodata))] = np.nan
    return values * float(da.attrs.get("scale_factor", 1.0)) + float(
        da.attrs.get("add_offset", 0.0)
    )


def _digest(parts: list[str], step: int | None, width: int) -> str:
    return hashlib.sha1(f"{parts}|{step}|{width}".encode()).hexdigest()[:8]
