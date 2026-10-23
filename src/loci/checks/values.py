"""Values within the plausible range of a declared (or clearly named) variable."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from pandas.api import types as ptypes

from loci.checks.core import Layer, result
from loci.models import CheckResult
from loci.summary.common import sig
from loci.summary.raster import valid_mask


@dataclass(frozen=True)
class Variable:
    name: str
    label: str
    low: float
    high: float
    outside: Literal["warn", "fail"]
    why: str


VARIABLES: dict[str, Variable] = {
    v.name: v
    for v in (
        Variable("ndvi", "NDVI", -1, 1, "fail", "a normalised difference"),
        Variable("ndwi", "NDWI", -1, 1, "fail", "a normalised difference"),
        Variable("ndmi", "NDMI", -1, 1, "fail", "a normalised difference"),
        Variable("nbr", "NBR", -1, 1, "fail", "a normalised difference"),
        Variable("savi", "SAVI", -1.5, 1.5, "fail", "bounded by ±(1 + L) with L = 0.5"),
        Variable("evi", "EVI", -1, 1, "warn", "the usual range; bright surfaces can exceed it"),
        Variable("reflectance", "Reflectance", 0, 1, "warn", "physical; correction can stray"),
        Variable("fraction", "Fraction", 0, 1, "fail", "a share of a whole"),
        Variable("percent", "Percentage", 0, 100, "fail", "a share of a whole"),
        Variable("slope_degrees", "Slope", 0, 90, "fail", "degrees from horizontal"),
        Variable("aspect_degrees", "Aspect", 0, 360, "fail", "degrees clockwise from north"),
        Variable("elevation_m", "Elevation", -11_000, 8_850, "fail", "deepest trench to Everest"),
        Variable("flow_accumulation", "Flow accumulation", 0, math.inf, "fail", "a cell count"),
    )
}

INFERRABLE = frozenset({"ndvi", "ndwi", "ndmi", "nbr", "savi", "evi"})
"""Names unambiguous enough to infer the variable from a band or column name alone."""


def resolve_variable(name: str, declared: Mapping[str, str]) -> tuple[Variable, bool] | None:
    """The variable for a band or column, and whether it was inferred from the name."""
    if name in declared:
        return VARIABLES[declared[name]], False
    if name.lower() in INFERRABLE:
        return VARIABLES[name.lower()], True
    return None


def check_value_range(
    layer: Layer,
    target: str,
    variable: Variable,
    low: float | None,
    high: float | None,
    *,
    inferred: bool,
    method: str = "every value",
) -> CheckResult:
    """Compare the extremes of a band or column with the variable's range."""
    where = f"{'band' if layer.raster is not None else 'column'} '{target}'"
    bounds = f"[{_num(variable.low)}, {_num(variable.high)}]"
    inferred_note = "variable inferred from the name"
    details = {
        "target": target,
        "variable": variable.name,
        "range": [variable.low, variable.high if math.isfinite(variable.high) else None],
        "min": sig(low),
        "max": sig(high),
        "inferred": inferred,
        "method": method,
    }
    if low is None or high is None:
        return result(
            "value_range",
            "warn",
            [layer],
            f"No valid values in {where} to check against the {variable.label} range.",
            details=details,
            hint="Check the nodata value and masking.",
        )
    span = variable.high - variable.low
    tolerance = 1e-6 * (span if math.isfinite(span) else 1.0)
    if low < variable.low - tolerance or high > variable.high + tolerance:
        offending = low if low < variable.low - tolerance else high
        return result(
            "value_range",
            variable.outside,
            [layer],
            f"{variable.label} values in {where} reach {_num(offending)}, outside {bounds} "
            f"({variable.why}{'; ' + inferred_note if inferred else ''}).",
            details=details,
            hint="Check the formula, the scale and offset, and the nodata masking that produced "
            "these values.",
        )
    return result(
        "value_range",
        "pass",
        [layer],
        f"{variable.label} values in {where} span {_num(low)} to {_num(high)}, within "
        f"{bounds}{' (' + inferred_note + ')' if inferred else ''}.",
        details=details,
    )


def check_band_range(layer: Layer, band: str, variable: Variable, *, inferred: bool) -> CheckResult:
    """Range check on a raster band's physical values (stored * scale + offset)."""
    source = layer.stats_source()
    da = source.ds[band]
    values = np.asarray(da.values)
    nodata = da.rio.nodata
    valid = valid_mask(values, None if nodata is None else float(nodata))
    low = high = None
    if valid.any():
        scale = float(da.attrs.get("scale_factor", 1.0))
        offset = float(da.attrs.get("add_offset", 0.0))
        physical = values[valid].astype("float64") * scale + offset
        low, high = float(physical.min()), float(physical.max())
    method = "every pixel" if source.method == "exact" else f"{source.method} 1/{source.decimation}"
    return check_value_range(layer, band, variable, low, high, inferred=inferred, method=method)


def check_column_range(
    layer: Layer, column: str, variable: Variable, *, inferred: bool
) -> CheckResult:
    """Range check on a numeric attribute column; a non-numeric column fails."""
    values: pd.Series = layer.data[column]
    if not ptypes.is_numeric_dtype(values) or ptypes.is_bool_dtype(values):
        return result(
            "value_range",
            "fail",
            [layer],
            f"Column '{column}' holds {values.dtype} values, not numbers, so it cannot be a "
            f"{variable.label}.",
            details={"target": column, "variable": variable.name, "dtype": str(values.dtype)},
            hint="Check which column holds the variable.",
        )
    present = values.dropna().astype("float64")
    present = present[np.isfinite(present)]
    low = float(present.min()) if len(present) else None
    high = float(present.max()) if len(present) else None
    return check_value_range(layer, column, variable, low, high, inferred=inferred)


def _num(value: float) -> str:
    if math.isinf(value):
        return "∞" if value > 0 else "-∞"
    return f"{sig(value):g}"
