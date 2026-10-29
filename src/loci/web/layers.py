"""The workspace as map layers: what each data_ref is, how to draw it, and its values at a point.

Drawing is for display only. Tiles are reprojected to Web Mercator on the fly; values at a point
are read from the data in its own CRS, never from the drawn picture.

GeoTIFFs are tiled straight from the file, overviews and all. Every other raster (STAC loads,
time stacks, files with an assigned CRS) is tiled from one time step held in memory as physical
values, decimated if it would not fit.
"""

import math
import threading
from collections import OrderedDict
from functools import cache
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from pydantic import BaseModel, Field
from pyproj import CRS, Transformer
from rio_tiler.colormap import cmap
from rio_tiler.io import Reader, XarrayReader
from rio_tiler.models import ImageData
from rio_tiler.utils import render

from loci import provenance
from loci.errors import LociError
from loci.io import STAC_RASTER
from loci.io.formats import is_remote
from loci.io.stac import SCL_BAND, SCL_CLASSES, SCL_OBSCURED
from loci.models import Bounds, CheckStatus, ProducedBy, Provenance
from loci.render import stretch_limits
from loci.script import physical
from loci.summary.common import crs_info, extent, iso
from loci.summary.raster import decimation_for
from loci.tools.preview import preview, step_cloud_cover
from loci.workspace import RefRecord, Workspace

TILE_FORMATS = {"geotiff", "cog"}  # files rio-tiler reads directly, overviews and all
PREVIEW_WIDTH = 1024
MAX_FRAME_PIXELS = 16_000_000  # pixels per band of one time step held in memory for tiles
# Bytes a click may read for one band's values through time: a store kept one time step to a
# chunk, such as an hourly reanalysis, would otherwise be read whole for a single point.
POINT_BUDGET = {"local": 4 * 1024**3, "remote": 256 * 1024**2}
STRETCH_STEPS = 8  # dates a time stack's colour scale is taken from
STRETCH_PIXELS = 1_000_000
MAX_FRAMES = 8
MAX_FEATURES = 50_000
MAX_POINT_FEATURES = 5
HIGHLIGHT = (255, 92, 92)  # pixels outside a checked range

# Workspace objects (open files, dask graphs) are shared; read them one request at a time.
_lock = threading.Lock()
_frames: OrderedDict[tuple[str, str, tuple[str, ...], int | None], xr.DataArray] = OrderedDict()


class LayerInfo(BaseModel):
    """One data_ref as the layer list shows it."""

    data_ref: str
    kind: Literal["raster", "vector"]
    name: str
    produced_by: ProducedBy | None
    created_at: str
    validation: CheckStatus | None
    crs: str | None
    bounds_wgs84: Bounds | None
    bands: list[str] = Field(default_factory=list)
    times: int = 0
    drawable: bool
    note: str | None = None


class LayerDisplay(BaseModel):
    """How to draw a layer: map tiles, or GeoJSON.

    For a time stack, `url` draws time step `step`; `times` lists every step, and the same
    stretch suits them all, so one colour means one value on every date.
    """

    data_ref: str
    mode: Literal["tiles", "geojson"]
    url: str
    bands: list[str] | None = None
    stretch: tuple[float, float] | None = None
    colormap: str | None = None
    time: str | None = None
    times: list[str] = Field(default_factory=list)
    step: int | None = None
    note: str | None = None


class BandValues(BaseModel):
    name: str
    values: list[float | None]


class StepInfo(BaseModel):
    """One time step at a point: when, and what may hide the ground then.

    `cloud_cover` is the scene's share of cloud from item metadata (STAC loads). `scene_class`
    is the Sentinel-2 scene classification at the point, when an `scl` band was loaded, and
    `obscured` says it is cloud, shadow, cirrus, defective or no data there.
    """

    time: str
    cloud_cover: float | None = None
    scene_class: str | None = None
    obscured: bool = False


class PointValues(BaseModel):
    """Values of a layer at a point, read from the data in its own CRS."""

    data_ref: str
    lon: float
    lat: float
    x: float
    y: float
    crs: str
    steps: list[StepInfo] = Field(default_factory=list)
    bands: list[BandValues] = Field(default_factory=list)
    features: list[dict[str, str]] = Field(default_factory=list)


def layers(ws: Workspace) -> list[LayerInfo]:
    """Every data_ref in the workspace, newest first."""
    return [layer_info(ws, record) for record in ws.records()]


def layer_info(ws: Workspace, record: RefRecord) -> LayerInfo:
    found = provenance.read(ws, record.data_ref)
    with _lock:
        data = ws.open(record.data_ref)
    base: dict[str, Any] = {
        "data_ref": record.data_ref,
        "kind": record.kind,
        "name": _name(record, found),
        "produced_by": found.produced_by if found else None,
        "created_at": record.created_at.isoformat(timespec="seconds"),
        "validation": found.validation.status if found and found.validation else None,
    }
    if isinstance(data, xr.Dataset):
        crs = data.rio.crs
        bounds = _shown_bounds(extent(data.rio.bounds(), crs).bounds_wgs84) if crs else None
        return LayerInfo(
            **base,
            crs=_crs_name(crs),
            bounds_wgs84=bounds,
            bands=[str(name) for name in data.data_vars],
            times=data.sizes.get("time", 0),
            drawable=crs is not None,
            note=None if crs else "No CRS, so it cannot be placed on the map.",
        )
    crs = data.crs
    bounds = extent(tuple(data.total_bounds), crs).bounds_wgs84 if crs and len(data) else None
    return LayerInfo(
        **base,
        crs=_crs_name(crs),
        bounds_wgs84=bounds,
        drawable=crs is not None and len(data) > 0,
        note=None if crs else "No CRS, so it cannot be placed on the map.",
    )


def display(
    ws: Workspace, data_ref: str, *, bands: list[str] | None = None, time: str | None = None
) -> LayerDisplay:
    """How to draw a layer. Rasters are tiles, stretched like `preview` draws them; for a time
    stack the step is the one asked for, else the least cloudy."""
    record = ws.record(data_ref)
    if record.kind == "vector":
        return LayerDisplay(
            data_ref=data_ref, mode="geojson", url=f"/api/layers/{data_ref}/geojson"
        )
    with _lock:
        ds = ws.open(data_ref)
        assert isinstance(ds, xr.Dataset)
        if ds.rio.crs is None:
            raise LociError(f"{data_ref} has no CRS, so it cannot be placed on the map.")
        shown = preview(ws, data_ref, bands=bands, time=time, width=PREVIEW_WIDTH)
    chosen = shown.bands or []
    times = pd.DatetimeIndex(ds["time"].values) if "time" in ds.dims else None
    step = _nearest_step(times, shown.time) if times is not None else None
    stretch = shown.stretch
    if times is not None and record.source.format != STAC_RASTER.name:
        # One scale for every date: a seasonal cycle would saturate a scale taken from one date.
        # A satellite stack keeps its clearest date's, which leaves cloud out of the scale.
        with _lock:
            stretch = _stack_stretch(ds, chosen) or stretch
    query = "&".join(
        [f"bands={','.join(chosen)}"]
        + ([f"lo={stretch[0]}", f"hi={stretch[1]}"] if stretch else [])
        + ([f"step={step}"] if step is not None else [])
    )
    factor = 1 if _tileable(record, ds) else _factor(ds)
    note = (
        f"Drawn at 1/{factor} of the data's resolution so it fits in memory; values at a point "
        "are read at full resolution."
        if factor > 1
        else None
    )
    return LayerDisplay(
        data_ref=data_ref,
        mode="tiles",
        url=f"/api/layers/{data_ref}/tiles/{{z}}/{{x}}/{{y}}.png?{query}",
        bands=chosen,
        stretch=stretch,
        colormap="viridis" if len(chosen) == 1 else None,
        time=shown.time,
        times=[iso(t) for t in times] if times is not None else [],
        step=step,
        note=note,
    )


def tile(
    ws: Workspace,
    data_ref: str,
    z: int,
    x: int,
    y: int,
    *,
    bands: list[str],
    stretch: tuple[float, float] | None,
    step: int | None = None,
    outside: tuple[float, float] | None = None,
) -> bytes | None:
    """A 256-pixel PNG tile in Web Mercator, or None where the layer has no data.

    `step` picks the time step of a time stack. With `outside`, only pixels of the one band
    whose values fall outside that range are drawn, in a warning colour.
    """
    record = ws.record(data_ref)
    with _lock:
        ds = ws.open(data_ref)
    assert isinstance(ds, xr.Dataset)
    names = [str(name) for name in ds.data_vars]
    unknown = [b for b in bands if b not in names]
    if unknown or len(bands) not in (1, 3):
        raise LociError(f"Draw one band or three; {data_ref} has bands {names}.")
    if outside is not None and len(bands) != 1:
        raise LociError("Out-of-range pixels are drawn for one band at a time.")
    if _tileable(record, ds):
        if step is not None:
            raise LociError(f"{data_ref} has no time steps.")
        indexes = tuple(names.index(b) + 1 for b in bands)
        with Reader(record.source.uri) as src:
            if not src.tile_exists(x, y, z):
                return None
            image = src.tile(x, y, z, tilesize=256, indexes=indexes, unscale=True)
    else:
        reader = XarrayReader(_frame(ws, data_ref, ds, tuple(bands), step))
        if not reader.tile_exists(x, y, z):
            return None
        image = reader.tile(x, y, z, tilesize=256)
    if np.issubdtype(image.array.dtype, np.floating):
        # NaN is missing data even when the file declares no nodata value: draw it transparent.
        image.array.mask = np.ma.getmaskarray(image.array) | np.isnan(image.array.data)
    if outside is not None:
        return _outside(image, *outside)
    if stretch is not None:
        low, high = stretch
        if high <= low:  # a constant layer: draw it in the middle of the colour scale
            low, high = low - 1, high + 1
        image.rescale(in_range=[(low, high)] * len(bands))
    colormap = cmap.get("viridis") if len(bands) == 1 else None
    return image.render(img_format="PNG", colormap=colormap)


def point_values(ws: Workspace, data_ref: str, lon: float, lat: float) -> PointValues:
    """Values at a point: every band (through time) of a raster, or the features of a layer."""
    record = ws.record(data_ref)
    with _lock:
        data = ws.open(data_ref)
        if isinstance(data, xr.Dataset):
            return _raster_values(record, data, lon, lat)
        return _vector_values(data_ref, data, lon, lat)


def geojson(ws: Workspace, data_ref: str) -> str:
    """The layer as GeoJSON in WGS 84, for display."""
    with _lock:
        gdf = ws.open(data_ref)
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise LociError(f"{data_ref} is a raster; ask for its display instead.")
    if gdf.crs is None:
        raise LociError(f"{data_ref} has no CRS, so it cannot be placed on the map.")
    if len(gdf) > MAX_FEATURES:
        raise LociError(f"{data_ref} has {len(gdf)} features; the map draws up to {MAX_FEATURES}.")
    out = gdf.to_crs("EPSG:4326")
    return str(out.to_json(default=str))


def _tileable(record: RefRecord, ds: xr.Dataset) -> bool:
    """Whether rio-tiler can read the layer's own file: a GeoTIFF with its own CRS, no time."""
    return (
        record.source.format in TILE_FORMATS
        and record.crs_source == "file"
        and "time" not in ds.dims
    )


def _factor(ds: xr.Dataset) -> int:
    """The decimation that keeps one band of one time step within MAX_FRAME_PIXELS."""
    pixels = ds.sizes["x"] * ds.sizes["y"]
    return max(1, math.ceil(math.sqrt(pixels / MAX_FRAME_PIXELS)))


def _frame(
    ws: Workspace, data_ref: str, ds: xr.Dataset, bands: tuple[str, ...], step: int | None
) -> xr.DataArray:
    """One time step's bands as physical values in memory, NaN where missing, ready to tile.

    The latest few are kept, so panning and zooming read the data once.
    """
    key = (str(ws.root), data_ref, bands, step)
    with _lock:
        if key in _frames:
            _frames.move_to_end(key)
            return _frames[key]
        view = _for_display(_at_step(data_ref, ds, step), ds.rio.crs)
        factor = _factor(ds)
        if factor > 1:
            view = _decimate(view, factor)
        values = np.stack([np.asarray(physical(view[b]).values, dtype="float32") for b in bands])
        frame = (
            xr.DataArray(
                values,
                dims=("band", "y", "x"),
                coords={"band": list(range(1, len(bands) + 1)), "y": view["y"], "x": view["x"]},
            )
            .rio.write_crs(ds.rio.crs)
            .rio.write_nodata(np.nan)
        )
        _frames[key] = frame
        while len(_frames) > MAX_FRAMES:
            _frames.popitem(last=False)
    return frame


def _stack_stretch(ds: xr.Dataset, bands: list[str]) -> tuple[float, float] | None:
    """The 2nd and 98th percentiles of the bands over dates spread across the stack."""
    count = ds.sizes["time"]
    steps = sorted({int(i) for i in np.linspace(0, count - 1, min(count, STRETCH_STEPS))})
    sample = ds[bands].isel(time=steps)
    step = decimation_for(sample, STRETCH_PIXELS)
    sample = sample.isel(x=slice(None, None, step), y=slice(None, None, step))
    return stretch_limits([np.asarray(physical(sample[b]).values) for b in bands])


def _for_display(view: xr.Dataset, crs: CRS | None) -> xr.Dataset:
    """A view the tiler draws correctly, without changing the data: longitudes 0 to 360 shifted
    to -180 to 180, and rows that run south to north turned to run north to south."""
    if crs is not None and crs.is_geographic and float(view["x"].max()) > 180:
        view = view.assign_coords(x=((view["x"] + 180) % 360) - 180).sortby("x")
    if view.sizes["y"] > 1 and bool(view["y"][-1] > view["y"][0]):
        view = view.isel(y=slice(None, None, -1))
    return view


def _shown_bounds(bounds: Bounds | None) -> Bounds | None:
    """Longitude/latitude bounds within -180 to 180, as the map shows a 0 to 360 grid."""
    if bounds is None or bounds[2] <= 180:
        return bounds
    west, south, east, north = bounds
    if west >= 180:
        return (west - 360, south, east - 360, north)
    return (-180.0, south, 180.0, north)


def _at_step(data_ref: str, ds: xr.Dataset, step: int | None) -> xr.Dataset:
    if "time" not in ds.dims:
        if step is not None:
            raise LociError(f"{data_ref} has no time steps.")
        return ds
    count = ds.sizes["time"]
    if step is None or not 0 <= step < count:
        raise LociError(f"Pick a time step of {data_ref} from 0 to {count - 1} with step=.")
    return ds.isel(time=step)


def _decimate(ds: xr.Dataset, factor: int) -> xr.Dataset:
    """Every `factor`-th pixel, placed at the centre of the block it stands for."""
    view = ds.isel(x=slice(None, None, factor), y=slice(None, None, factor))
    shift = (factor - 1) / 2
    dx = float(ds["x"][1] - ds["x"][0]) if ds.sizes["x"] > 1 else 0.0
    dy = float(ds["y"][1] - ds["y"][0]) if ds.sizes["y"] > 1 else 0.0
    return view.assign_coords(x=view["x"] + shift * dx, y=view["y"] + shift * dy)


def _outside(image: ImageData, low: float, high: float) -> bytes:
    """Only the pixels outside [low, high], in the warning colour; the rest transparent."""
    values = image.array[0]
    valid = ~np.ma.getmaskarray(values)
    data = np.asarray(values.data, dtype="float64")
    hit = valid & ((data < low) | (data > high))
    rgb = np.zeros((3, *hit.shape), dtype="uint8")
    for i, channel in enumerate(HIGHLIGHT):
        rgb[i][hit] = channel
    return render(rgb, mask=np.where(hit, 255, 0).astype("uint8"), img_format="PNG")


def _nearest_step(times: pd.DatetimeIndex, when: str | None) -> int:
    if when is None:
        return 0
    return int(np.argmin(np.abs((times - pd.Timestamp(when)).total_seconds())))


def _raster_values(record: RefRecord, ds: xr.Dataset, lon: float, lat: float) -> PointValues:
    crs = ds.rio.crs
    if crs is None:
        raise LociError(f"{record.data_ref} has no CRS, so a point cannot be placed on it.")
    x, y = _to_crs(crs).transform(lon, lat)
    left, bottom, right, top = ds.rio.bounds()
    if crs.is_geographic and right > 180 and x < 0:
        x += 360  # the grid runs from 0 to 360
    if not (left <= x <= right and bottom <= y <= top):
        raise LociError("The point is outside this layer.")
    _within_budget(record, ds)
    at = ds.sel(x=x, y=y, method="nearest")
    bands = []
    for name in at.data_vars:
        band = at[name]
        values = np.atleast_1d(np.asarray(physical(band.astype("float64")).values))
        # Packed values are only as precise as their scale: 0.0001 * 1000 - 0.1 is 0, not 1e-17.
        scale = float(band.attrs.get("scale_factor", 1.0))
        if scale != 1.0:
            values = np.round(values, max(0, -math.floor(math.log10(abs(scale)))) + 2)
        bands.append(
            BandValues(name=str(name), values=[None if np.isnan(v) else float(v) for v in values])
        )
    return PointValues(
        data_ref=record.data_ref,
        lon=lon,
        lat=lat,
        x=float(x),
        y=float(y),
        crs=_crs_name(crs) or "",
        steps=_steps(record, at) if "time" in at.dims else [],
        bands=bands,
    )


def _within_budget(record: RefRecord, ds: xr.Dataset) -> None:
    """Refuse a point's values through time when the data's stored chunks would make that a
    large read: each time step costs a whole chunk, however small the area loaded from it."""
    first = next(iter(ds.data_vars.values()), None)
    stored = first.encoding.get("preferred_chunks") if first is not None else None
    if first is None or "time" not in first.dims or not stored:
        return  # stored whole or in strips: a point reads little more than its own values
    rows, cols = stored.get("y", ds.sizes["y"]), stored.get("x", ds.sizes["x"])
    needed = ds.sizes["time"] * rows * cols * first.dtype.itemsize * len(ds.data_vars)
    where = "remote" if is_remote(record.source.uri) else "local"
    if needed > POINT_BUDGET[where]:
        raise LociError(
            f"Reading this point through time would read about {needed / 1024**3:.1f} GB, "
            f"because {first.name} is stored {rows} by {cols} cells at a time.",
            hint="Load a shorter period with select=, or a store chunked for time series.",
        )


def _steps(record: RefRecord, at: xr.Dataset) -> list[StepInfo]:
    """Each time step at the point, with the scene's cloud cover and the SCL class if known."""
    times = pd.DatetimeIndex(at["time"].values)
    clouds = step_cloud_cover(record, times)
    classes: list[int | None] = [None] * len(times)
    if SCL_BAND in at.data_vars:
        scl = np.asarray(at[SCL_BAND].values, dtype="float64")
        classes = [None if np.isnan(v) else int(v) for v in scl]
    steps = []
    for i, when in enumerate(times):
        cover = None if clouds is None or pd.isna(clouds.iloc[i]) else float(clouds.iloc[i])
        code = classes[i]
        steps.append(
            StepInfo(
                time=iso(when),
                cloud_cover=cover,
                scene_class=None if code is None else SCL_CLASSES.get(code, f"class {code}"),
                obscured=code in SCL_OBSCURED,
            )
        )
    return steps


def _vector_values(data_ref: str, gdf: gpd.GeoDataFrame, lon: float, lat: float) -> PointValues:
    if gdf.crs is None:
        raise LociError(f"{data_ref} has no CRS, so a point cannot be placed on it.")
    x, y = _to_crs(gdf.crs).transform(lon, lat)
    hits = gdf[gdf.intersects(gpd.points_from_xy([x], [y])[0])].head(MAX_POINT_FEATURES)
    features = [
        {str(k): str(v) for k, v in row.items() if k != gdf.geometry.name}
        for _, row in hits.iterrows()
    ]
    return PointValues(
        data_ref=data_ref,
        lon=lon,
        lat=lat,
        x=float(x),
        y=float(y),
        crs=_crs_name(gdf.crs) or "",
        features=features,
    )


def _name(record: RefRecord, found: Provenance | None) -> str:
    """A short human name: the file a run wrote, the file loaded, or what a search found."""
    if found is not None and found.produced_by == "run" and found.path:
        return Path(found.path).name
    if found is not None and found.produced_by == "stac_search":
        p = found.parameters
        return f"Search: {p.get('collection')}, {p.get('start')} to {p.get('end')}"
    if record.source.format == "stac-raster":
        bands = record.source.options.bands or []
        return "STAC load: " + ", ".join(str(b) for b in bands)
    return Path(record.source.uri).name or record.data_ref


def _crs_name(crs: object) -> str | None:
    info = crs_info(crs) if crs is not None else None
    if info is None:
        return None
    return f"EPSG:{info.epsg}" if info.epsg else info.name


def _to_crs(crs: object) -> Transformer:
    return _transformer(CRS.from_user_input(crs).to_wkt())


@cache
def _transformer(wkt: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", CRS.from_wkt(wkt), always_xy=True)
