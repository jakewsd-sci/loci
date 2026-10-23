"""The workspace as map layers: what each data_ref is, how to draw it, and its values at a point.

Drawing is for display only. Tiles are reprojected to Web Mercator on the fly; values at a point
are read from the data in its own CRS, never from the drawn picture.
"""

import threading
from functools import cache
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import numpy as np
import xarray as xr
from pydantic import BaseModel, Field
from pyproj import CRS, Transformer
from rio_tiler.colormap import cmap
from rio_tiler.io import Reader

from loci import provenance
from loci.errors import LociError
from loci.models import Bounds, CheckStatus, ProducedBy, Provenance
from loci.script import physical
from loci.summary.common import crs_info, extent
from loci.tools.preview import preview
from loci.workspace import RefRecord, Workspace

TILE_FORMATS = {"geotiff", "cog"}  # files rio-tiler reads directly, overviews and all
PREVIEW_WIDTH = 1024
MAX_FEATURES = 50_000
MAX_POINT_FEATURES = 5

# Workspace objects (open files, dask graphs) are shared; read them one request at a time.
_lock = threading.Lock()


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
    """How to draw a layer: tiles, one georeferenced picture, or GeoJSON."""

    data_ref: str
    mode: Literal["tiles", "image", "geojson"]
    url: str
    coordinates: list[tuple[float, float]] | None = None  # image corners: TL, TR, BR, BL
    bands: list[str] | None = None
    stretch: tuple[float, float] | None = None
    colormap: str | None = None
    time: str | None = None
    note: str | None = None


class BandValues(BaseModel):
    name: str
    values: list[float | None]


class PointValues(BaseModel):
    """Values of a layer at a point, read from the data in its own CRS."""

    data_ref: str
    lon: float
    lat: float
    x: float
    y: float
    crs: str
    times: list[str] = Field(default_factory=list)
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
        bounds = extent(data.rio.bounds(), crs).bounds_wgs84 if crs else None
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
    """How to draw a layer: rasters read from a file as tiles, other rasters as one picture."""
    record = ws.record(data_ref)
    if record.kind == "vector":
        return LayerDisplay(
            data_ref=data_ref, mode="geojson", url=f"/api/layers/{data_ref}/geojson"
        )
    with _lock:
        shown = preview(ws, data_ref, bands=bands, time=time, width=PREVIEW_WIDTH)
        ds = ws.open(data_ref)
    assert isinstance(ds, xr.Dataset)
    if ds.rio.crs is None:
        raise LociError(f"{data_ref} has no CRS, so it cannot be placed on the map.")
    chosen = shown.bands or []
    common: dict[str, Any] = {
        "data_ref": data_ref,
        "bands": chosen,
        "stretch": shown.stretch,
        "colormap": "viridis" if len(chosen) == 1 else None,
        "time": shown.time,
    }
    if _tileable(record, ds):
        query = "&".join(
            [f"bands={','.join(chosen)}"]
            + ([f"lo={shown.stretch[0]}", f"hi={shown.stretch[1]}"] if shown.stretch else [])
        )
        url = f"/api/layers/{data_ref}/tiles/{{z}}/{{x}}/{{y}}.png?{query}"
        return LayerDisplay(**common, mode="tiles", url=url)
    corners = _corners_wgs84(shown.extent.bounds, ds.rio.crs)
    url = f"/api/previews/{Path(shown.path).name}"
    note = "One preview picture at reduced resolution; zooming in will not add detail."
    return LayerDisplay(**common, mode="image", url=url, coordinates=corners, note=note)


def tile(
    ws: Workspace,
    data_ref: str,
    z: int,
    x: int,
    y: int,
    *,
    bands: list[str],
    stretch: tuple[float, float] | None,
) -> bytes | None:
    """A 256-pixel PNG tile in Web Mercator, or None where the layer has no data."""
    record = ws.record(data_ref)
    with _lock:
        ds = ws.open(data_ref)
    assert isinstance(ds, xr.Dataset)
    if not _tileable(record, ds):
        raise LociError(f"{data_ref} is drawn as one preview picture, not as tiles.")
    names = [str(name) for name in ds.data_vars]
    unknown = [b for b in bands if b not in names]
    if unknown or len(bands) not in (1, 3):
        raise LociError(f"Draw one band or three; {data_ref} has bands {names}.")
    indexes = tuple(names.index(b) + 1 for b in bands)
    with Reader(record.source.uri) as src:
        if not src.tile_exists(x, y, z):
            return None
        image = src.tile(x, y, z, tilesize=256, indexes=indexes, unscale=True)
    if stretch is not None:
        image.rescale(in_range=[stretch] * len(indexes))
    colormap = cmap.get("viridis") if len(indexes) == 1 else None
    return image.render(img_format="PNG", colormap=colormap)


def point_values(ws: Workspace, data_ref: str, lon: float, lat: float) -> PointValues:
    """Values at a point: every band (through time) of a raster, or the features of a layer."""
    with _lock:
        data = ws.open(data_ref)
        if isinstance(data, xr.Dataset):
            return _raster_values(data_ref, data, lon, lat)
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
    return (
        record.source.format in TILE_FORMATS
        and record.crs_source == "file"
        and "time" not in ds.dims
    )


def _raster_values(data_ref: str, ds: xr.Dataset, lon: float, lat: float) -> PointValues:
    crs = ds.rio.crs
    if crs is None:
        raise LociError(f"{data_ref} has no CRS, so a point cannot be placed on it.")
    x, y = _to_crs(crs).transform(lon, lat)
    left, bottom, right, top = ds.rio.bounds()
    if not (left <= x <= right and bottom <= y <= top):
        raise LociError("The point is outside this layer.")
    at = ds.sel(x=x, y=y, method="nearest")
    times = [str(t)[:19] for t in at["time"].values] if "time" in at.dims else []
    bands = []
    for name in at.data_vars:
        values = np.atleast_1d(np.asarray(physical(at[name]).values, dtype="float64"))
        bands.append(
            BandValues(name=str(name), values=[None if np.isnan(v) else float(v) for v in values])
        )
    return PointValues(
        data_ref=data_ref,
        lon=lon,
        lat=lat,
        x=float(x),
        y=float(y),
        crs=_crs_name(crs) or "",
        times=times,
        bands=bands,
    )


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


def _corners_wgs84(bounds: Bounds, crs: object) -> list[tuple[float, float]]:
    """The four corners of native bounds in lon/lat: top-left, top-right, bottom-right, -left."""
    left, bottom, right, top = bounds
    to_wgs84 = Transformer.from_crs(CRS.from_user_input(crs), "EPSG:4326", always_xy=True)
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    return [tuple(round(float(v), 7) for v in to_wgs84.transform(x, y)) for x, y in corners]  # type: ignore[misc]
