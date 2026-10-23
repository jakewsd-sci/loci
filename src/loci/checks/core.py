"""What checks share: the Layer they inspect, result construction and the report."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
import shapely
import xarray as xr
from pydantic import JsonValue
from pyproj import CRS

from loci.io import Data
from loci.models import CheckResult, CheckStatus, CrsSource, ValidationReport
from loci.summary.raster import StatsSource, plan_stats

SEVERITY: dict[CheckStatus, int] = {"pass": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True)
class Layer:
    """A dataset under check.

    `name` labels results (the validate tool uses the data_ref). `sample` holds the pixels that
    raster statistics come from (see summary.raster.StatsSource); it is planned on demand if absent.
    """

    name: str
    data: Data
    crs_source: CrsSource | None = None
    sample: StatsSource | None = None

    @property
    def raster(self) -> xr.Dataset | None:
        return self.data if isinstance(self.data, xr.Dataset) else None

    @property
    def vector(self) -> gpd.GeoDataFrame | None:
        return self.data if isinstance(self.data, gpd.GeoDataFrame) else None

    def stats_source(self) -> StatsSource:
        if self.sample is not None:
            return self.sample
        ds = self.raster
        if ds is None:
            raise TypeError(f"{self.name} is not a raster")
        return plan_stats(ds)


def layer_crs(layer: Layer) -> CRS | None:
    crs = layer.raster.rio.crs if layer.raster is not None else layer.data.crs
    return None if crs is None else CRS.from_user_input(crs)


def layer_bounds(layer: Layer) -> tuple[float, float, float, float] | None:
    """Bounds in the layer's own CRS; None for a vector with no non-empty geometry."""
    if layer.raster is not None:
        minx, miny, maxx, maxy = layer.raster.rio.bounds()
        return (float(minx), float(miny), float(maxx), float(maxy))
    geoms = layer.data.geometry.to_numpy()
    present = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
    if not present.any():
        return None
    minx, miny, maxx, maxy = shapely.total_bounds(geoms[present])
    return (float(minx), float(miny), float(maxx), float(maxy))


def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.name


def result(
    check: str,
    status: CheckStatus,
    layers: Iterable[Layer],
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    hint: str | None = None,
) -> CheckResult:
    """A CheckResult; the hint is kept only when the status is not pass."""
    return CheckResult(
        check=check,
        status=status,
        data_refs=[layer.name for layer in layers],
        message=message,
        details={k: _json(v) for k, v in (details or {}).items()},
        hint=None if status == "pass" else hint,
    )


def worst(statuses: Iterable[CheckStatus]) -> CheckStatus:
    return max(statuses, key=SEVERITY.__getitem__, default="pass")


def build_report(data_refs: Sequence[str], checks: Sequence[CheckResult]) -> ValidationReport:
    counts: dict[CheckStatus, int] = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        counts[check.status] += 1
    return ValidationReport(
        status=worst(c.status for c in checks),
        data_refs=list(data_refs),
        counts=counts,
        checks=list(checks),
    )


def _json(value: Any) -> JsonValue:
    """Plain JSON values: numpy scalars to Python, tuples to lists, recursively."""
    if isinstance(value, np.generic):
        return _json(value.item())
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json(v) for v in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
