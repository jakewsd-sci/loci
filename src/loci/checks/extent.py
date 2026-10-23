"""Extents overlap where an operation assumes they do.

Compares bounding boxes in the first input's CRS. For each pair, the smaller box should lie within
the larger: no overlap fails, partial overlap warns.
"""

from collections.abc import Sequence
from itertools import combinations

from pyproj import Transformer

from loci.checks.core import Layer, layer_bounds, layer_crs, result
from loci.models import CheckResult
from loci.summary.common import sig

Box = tuple[float, float, float, float]
CONTAINED = 0.999
MAX_PAIRS_IN_DETAILS = 5


def check_extents_overlap(layers: Sequence[Layer]) -> CheckResult:
    crss = [layer_crs(layer) for layer in layers]
    missing = [layer.name for layer, crs in zip(layers, crss, strict=True) if crs is None]
    if missing:
        return result(
            "extents_overlap",
            "warn",
            layers,
            f"Cannot compare extents: {', '.join(missing)} {'has' if len(missing) == 1 else 'have'}"
            " no CRS.",
            hint="Give every input a CRS first (see crs_defined).",
        )
    target = crss[0]
    assert target is not None
    boxes: list[Box] = []
    for layer, crs in zip(layers, crss, strict=True):
        bounds = layer_bounds(layer)
        if bounds is None:
            return result(
                "extents_overlap",
                "warn",
                layers,
                f"{layer.name} has no geometries, so its extent is undefined.",
                hint="Check why the layer is empty before combining it.",
            )
        assert crs is not None
        if not crs.equals(target, ignore_axis_order=True):
            transformer = Transformer.from_crs(crs, target, always_xy=True)
            bounds = transformer.transform_bounds(*bounds, densify_pts=21)
        boxes.append(bounds)

    pairs = []
    for (a, box_a), (b, box_b) in combinations(zip(layers, boxes, strict=True), 2):
        small, large = (a, b) if _area(box_a) <= _area(box_b) else (b, a)
        small_box, large_box = (box_a, box_b) if small is a else (box_b, box_a)
        pairs.append((small.name, large.name, _fraction_inside(small_box, large_box)))
    details = {
        "coverage": [
            {"smaller": s, "larger": lg, "fraction_inside": sig(f)}
            for s, lg, f in pairs[:MAX_PAIRS_IN_DETAILS]
        ]
    }

    disjoint = [p for p in pairs if p[2] == 0]
    partial = [p for p in pairs if 0 < p[2] < CONTAINED]
    if disjoint:
        s, lg, _ = disjoint[0]
        more = f" ({len(disjoint)} pairs in all)" if len(disjoint) > 1 else ""
        return result(
            "extents_overlap",
            "fail",
            layers,
            f"{s} and {lg} do not overlap{more}.",
            details=details,
            hint="Check that the inputs cover the same place and are in the CRS they claim; a "
            "wrong CRS often shows up as disjoint extents.",
        )
    if partial:
        s, lg, f = min(partial, key=lambda p: p[2])
        return result(
            "extents_overlap",
            "warn",
            layers,
            f"Only {f:.0%} of the extent of {s} lies within the extent of {lg}.",
            details=details,
            hint="Results will cover only the overlap. Clip to it explicitly, or choose inputs "
            "that cover the whole area of interest.",
        )
    return result(
        "extents_overlap",
        "pass",
        layers,
        "Extents overlap: in each pair, the smaller lies within the larger.",
        details=details,
    )


def _area(box: Box) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def _fraction_inside(small: Box, large: Box) -> float:
    """Share of `small` inside `large`. Zero-area boxes (points, lines) count as all or nothing."""
    if _area(small) == 0:
        inside = (
            large[0] <= small[0]
            and small[2] <= large[2]
            and large[1] <= small[1]
            and small[3] <= large[3]
        )
        return 1.0 if inside else 0.0
    overlap = (
        max(min(small[2], large[2]) - max(small[0], large[0]), 0.0),
        max(min(small[3], large[3]) - max(small[1], large[1]), 0.0),
    )
    return overlap[0] * overlap[1] / _area(small)
