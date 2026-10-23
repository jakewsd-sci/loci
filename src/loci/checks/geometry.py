"""Vector checks: valid, non-empty geometries, and area conserved through overlays and dissolves."""

import math
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from loci.checks.core import Layer, layer_crs, result
from loci.models import CheckResult
from loci.summary.common import sig
from loci.summary.vector import invalid_geometries


def check_geometry_valid(layer: Layer) -> CheckResult:
    """fail on invalid geometries; warn on empty or missing ones, or an empty layer."""
    gdf = _vector(layer)
    geoms = gdf.geometry.to_numpy()
    missing = shapely.is_missing(geoms)
    empty = shapely.is_empty(geoms) & ~missing
    present = ~missing & ~empty
    invalid = invalid_geometries(geoms, present)
    n = len(gdf)
    details = {
        "features": n,
        "invalid": invalid.count,
        "empty": int(empty.sum()),
        "missing": int(missing.sum()),
        "examples": [e.model_dump() for e in invalid.examples],
    }
    if invalid.count:
        first = invalid.examples[0]
        return result(
            "geometry_valid",
            "fail",
            [layer],
            f"{invalid.count} of {n} features {'has' if invalid.count == 1 else 'have'} an invalid"
            f" geometry (first: row {first.row}, {first.reason}).",
            details=details,
            hint="Repair them as an explicit, recorded step (for example with shapely.make_valid) "
            "before overlays or area calculations.",
        )
    blank = int(empty.sum() + missing.sum())
    if blank:
        return result(
            "geometry_valid",
            "warn",
            [layer],
            f"{blank} of {n} features {'has' if blank == 1 else 'have'} an empty or missing "
            "geometry.",
            details=details,
            hint="Drop or fix them explicitly if they are not expected.",
        )
    if n == 0:
        return result(
            "geometry_valid",
            "warn",
            [layer],
            "The layer has no features.",
            details=details,
            hint="Check why the layer is empty.",
        )
    return result(
        "geometry_valid",
        "pass",
        [layer],
        f"All {n} geometries are valid and non-empty.",
        details=details,
    )


def feature_areas(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Area of each feature in square metres: geodesic for geographic CRSs, planar otherwise.

    Missing and empty geometries have area 0.
    """
    crs = CRS.from_user_input(gdf.crs)
    geoms = gdf.geometry.to_numpy()
    if crs.is_geographic:
        geod = crs.get_geod()
        assert geod is not None
        areas = np.array(
            [
                abs(geod.geometry_area_perimeter(g)[0]) if g is not None and not g.is_empty else 0.0
                for g in geoms
            ]
        )
    else:
        metres = crs.axis_info[0].unit_conversion_factor
        areas = shapely.area(geoms).astype("float64") * metres**2
    return np.nan_to_num(areas)


def check_area_conserved(
    output: Layer, reference: Layer, *, by: str | None = None, tolerance: float = 1e-4
) -> CheckResult:
    """fail if `output`'s area differs from `reference`'s by more than `tolerance` (relative).

    With `by`, areas are compared per value of that column, which both layers must have: for
    example land-use pieces per catchment against the catchments themselves.
    """
    layers = [output, reference]
    out_crs, ref_crs = layer_crs(output), layer_crs(reference)
    if out_crs is None or ref_crs is None or not out_crs.equals(ref_crs, ignore_axis_order=True):
        return result(
            "area_conserved",
            "fail",
            layers,
            f"Cannot compare areas: {output.name} and {reference.name} do not share a CRS.",
            hint="Reproject to one CRS first; comparing areas across CRSs mixes distortions.",
        )
    out_gdf, ref_gdf = _vector(output), _vector(reference)
    out_areas, ref_areas = feature_areas(out_gdf), feature_areas(ref_gdf)

    if by is None:
        out_total, ref_total = float(out_areas.sum()), float(ref_areas.sum())
        difference = _relative(out_total, ref_total)
        details = {
            "output_m2": sig(out_total),
            "reference_m2": sig(ref_total),
            "relative_difference": sig(difference),
            "tolerance": tolerance,
        }
        if difference <= tolerance:
            return result(
                "area_conserved",
                "pass",
                layers,
                f"Total area of {output.name} ({_area(out_total)}) matches {reference.name} "
                f"within {_percent(tolerance)}.",
                details=details,
            )
        return result(
            "area_conserved",
            "fail",
            layers,
            f"Total area of {output.name} is {_area(out_total)} but {reference.name} has "
            f"{_area(ref_total)}: {_percent(difference)} apart (tolerance {_percent(tolerance)}).",
            details=details,
            hint=_AREA_HINT,
        )

    out_groups = pd.Series(out_areas, index=out_gdf[by].to_numpy()).groupby(level=0).sum()
    ref_groups = pd.Series(ref_areas, index=ref_gdf[by].to_numpy()).groupby(level=0).sum()
    keys = sorted(set(out_groups.index) | set(ref_groups.index), key=str)
    differences = {
        k: _relative(float(out_groups.get(k, 0.0)), float(ref_groups.get(k, 0.0))) for k in keys
    }
    outside = {k: d for k, d in differences.items() if d > tolerance}
    worst = max(differences, key=differences.__getitem__) if differences else None
    group_details: dict[str, Any] = {
        "by": by,
        "groups": len(keys),
        "groups_outside": len(outside),
        "tolerance": tolerance,
    }
    if worst is not None:
        group_details["worst"] = {
            "group": str(worst),
            "output_m2": sig(float(out_groups.get(worst, 0.0))),
            "reference_m2": sig(float(ref_groups.get(worst, 0.0))),
            "relative_difference": sig(differences[worst]),
        }
    if not outside:
        return result(
            "area_conserved",
            "pass",
            layers,
            f"Area matches {reference.name} for all {len(keys)} groups of '{by}' within "
            f"{_percent(tolerance)}.",
            details=group_details,
        )
    assert worst is not None
    return result(
        "area_conserved",
        "fail",
        layers,
        f"Area differs from {reference.name} for {len(outside)} of {len(keys)} groups of "
        f"'{by}' (worst: '{worst}', {_area(float(out_groups.get(worst, 0.0)))} against "
        f"{_area(float(ref_groups.get(worst, 0.0)))}).",
        details=group_details,
        hint=_AREA_HINT,
    )


_AREA_HINT = (
    "Look for gaps, overlaps and slivers: overlapping input polygons are counted twice, and "
    "parts outside the overlay are dropped."
)


def _vector(layer: Layer) -> gpd.GeoDataFrame:
    if layer.vector is None:
        raise TypeError(f"{layer.name} is not a vector layer")
    return layer.vector


def _relative(value: float, reference: float) -> float:
    if reference == 0:
        return 0.0 if value == 0 else math.inf
    return abs(value - reference) / reference


def _area(m2: float) -> str:
    if m2 >= 1e6:
        return f"{m2 / 1e6:,.4g} km²"
    if m2 >= 1e4:
        return f"{m2 / 1e4:,.4g} ha"
    return f"{m2:,.4g} m²"


def _percent(fraction: float) -> str:
    return "∞%" if math.isinf(fraction) else f"{fraction * 100:.3g}%"
