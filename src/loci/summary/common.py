"""Helpers shared by raster and vector summaries: size limits, rounding, CRS, extent, time."""

import math
from collections.abc import Iterable
from typing import Any

import pandas as pd
from pydantic import BaseModel
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError

from loci.models import Bounds, CRSInfo, Extent

SIGNIFICANT_DIGITS = 6
COORDINATE_DECIMALS = 6
MAX_TEXT = 40
LIST_BUDGET_BYTES = 3000
"""Most JSON a summary's band or attribute list may use, keeping a whole summary near 4 KB."""


def take_within_budget[T: BaseModel](
    items: Iterable[T], *, max_items: int, budget_bytes: int = LIST_BUDGET_BYTES
) -> list[T]:
    """The leading items whose JSON fits the budget: at least one, at most max_items.

    `items` is consumed lazily, so at most one item beyond those returned is ever built.
    """
    taken: list[T] = []
    used = 0
    for item in items:
        size = len(item.model_dump_json())
        if taken and (len(taken) == max_items or used + size > budget_bytes):
            break
        taken.append(item)
        used += size
    return taken


def sig(value: Any, digits: int = SIGNIFICANT_DIGITS) -> float | None:
    """Round to significant figures. None for missing, NaN or infinite values."""
    if value is None:
        return None
    x = float(value)
    if not math.isfinite(x):
        return None
    if x == 0:
        return 0.0
    return round(x, digits - 1 - math.floor(math.log10(abs(x))))


def truncate(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def iso(timestamp: Any) -> str:
    """ISO 8601 text: the date alone at midnight, otherwise to the second."""
    ts = pd.Timestamp(timestamp)
    if ts == ts.normalize():
        return ts.date().isoformat()
    return ts.floor("s").isoformat()


def crs_info(crs: Any) -> CRSInfo | None:
    """Name, EPSG code, kind and units of any CRS pyproj understands (or None)."""
    if crs is None:
        return None
    parsed = CRS.from_user_input(crs)
    units = parsed.axis_info[0].unit_name if parsed.axis_info else None
    return CRSInfo(
        epsg=parsed.to_epsg(),
        name=parsed.name,
        is_geographic=parsed.is_geographic,
        units=units,
    )


def extent(bounds: Any, crs: Any) -> Extent:
    """Bounds in the data's CRS, plus WGS 84 bounds when the CRS is known and transformable."""
    native = _round_bounds(bounds)
    return Extent(bounds=native, bounds_wgs84=_wgs84_bounds(native, crs))


def _wgs84_bounds(bounds: Bounds, crs: Any) -> Bounds | None:
    if crs is None:
        return None
    try:
        transformer = Transformer.from_crs(CRS.from_user_input(crs), 4326, always_xy=True)
        west, south, east, north = transformer.transform_bounds(*bounds, densify_pts=21)
    except ProjError:
        return None
    if not all(math.isfinite(v) for v in (west, south, east, north)):
        return None
    return _round_bounds((west, south, east, north))


def _round_bounds(bounds: Any) -> Bounds:
    minx, miny, maxx, maxy = (round(float(b), COORDINATE_DECIMALS) for b in bounds)
    return (minx, miny, maxx, maxy)
