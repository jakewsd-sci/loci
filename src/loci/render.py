"""Draw small PNG pictures of rasters and vector layers, for a vision-capable model to look at.

Nothing here knows about data refs: rasters arrive as arrays with NaN for invalid pixels, vectors
as GeoDataFrames. Matplotlib is used without pyplot, so no global state or display is involved.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.image import imsave

COLORMAP = "viridis"
STRETCH_PERCENTILES = (2.0, 98.0)
FILL = "#3d7ea6"
EDGE = "#1b2a30"


@dataclass(frozen=True)
class Rendered:
    width: int
    height: int
    stretch: tuple[float, float] | None = None


def stretch_limits(arrays: list[np.ndarray]) -> tuple[float, float] | None:
    """The 2nd and 98th percentiles of all finite values together, or None if there are none.

    The two are equal for a constant picture.
    """
    finite = [a[np.isfinite(a)] for a in arrays]
    values = np.concatenate(finite) if finite else np.array([])
    if values.size == 0:
        return None
    low, high = (float(v) for v in np.percentile(values, STRETCH_PERCENTILES))
    return low, high


def render_raster(bands: list[np.ndarray], path: Path) -> Rendered:
    """One band in the viridis colormap, or three as red, green and blue, with one shared stretch.

    Pixels that are NaN in any band are transparent. A constant picture is drawn mid-tone.
    """
    if len(bands) not in (1, 3):
        raise ValueError("render_raster takes one band or three")
    limits = stretch_limits(bands)
    low, high = limits or (0.0, 1.0)
    if high > low:
        scaled = [np.clip((np.nan_to_num(b, nan=low) - low) / (high - low), 0, 1) for b in bands]
    else:
        scaled = [np.full(b.shape, 0.5) for b in bands]
    valid = np.logical_and.reduce([np.isfinite(b) for b in bands])
    if len(bands) == 3:
        rgba = np.dstack([*scaled, valid.astype("float64")])
    else:
        rgba = colormaps[COLORMAP](scaled[0])
        rgba[..., 3] = valid
    imsave(path, rgba)
    height, width = valid.shape
    return Rendered(width=width, height=height, stretch=limits)


def render_vector(
    gdf: gpd.GeoDataFrame, path: Path, *, width: int, column: str | None = None
) -> Rendered:
    """Features drawn to scale on white, optionally coloured by a column."""
    geoms = gdf.geometry.to_numpy()
    present = gdf[~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)]
    stretch_x = _x_stretch(present)
    minx, miny, maxx, maxy = _padded_bounds(present, stretch_x)
    ratio = (maxy - miny) / ((maxx - minx) * stretch_x)
    height = int(min(max(round(width * ratio), 16), 4 * width))

    fig = Figure(figsize=(width / 100, height / 100), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_axis_off()
    if len(present):
        style = {"column": column, "cmap": COLORMAP} if column else {"color": FILL}
        present.plot(ax=ax, edgecolor=EDGE, linewidth=0.6, markersize=14, **style)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect(1 / stretch_x)
    fig.savefig(path, dpi=100, facecolor="white")
    return Rendered(width=width, height=height)


def _geographic(gdf: gpd.GeoDataFrame) -> bool:
    return gdf.crs is not None and gdf.crs.is_geographic


def _x_stretch(gdf: gpd.GeoDataFrame) -> float:
    """Ground length of a unit of x relative to a unit of y: cos(latitude) for degrees, else 1."""
    if not _geographic(gdf) or len(gdf) == 0:
        return 1.0
    _, miny, _, maxy = gdf.total_bounds
    return math.cos(math.radians((miny + maxy) / 2))


def _padded_bounds(gdf: gpd.GeoDataFrame, stretch_x: float) -> tuple[float, float, float, float]:
    """Bounds with a margin of 3% of the larger ground span on every side.

    A point or an empty layer gets a small square around it.
    """
    if len(gdf) == 0:
        return (0.0, 0.0, 1.0, 1.0)
    minx, miny, maxx, maxy = (float(v) for v in gdf.total_bounds)
    span = max((maxx - minx) * stretch_x, maxy - miny)
    pad = 0.03 * span if span > 0 else (0.001 if _geographic(gdf) else 1.0)
    return minx - pad / stretch_x, miny - pad, maxx + pad / stretch_x, maxy + pad
