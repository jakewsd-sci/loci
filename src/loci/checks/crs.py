"""CRS defined, and the same across inputs that are combined."""

from collections.abc import Sequence

from pyproj import CRS

from loci.checks.core import Layer, crs_label, layer_crs, result
from loci.models import CheckResult

ORIGIN = {"file": "declared by the source", "argument": "given at load"}


def check_crs_defined(layer: Layer) -> CheckResult:
    """fail without a CRS; warn if loci assumed it; pass if declared or given."""
    crs = layer_crs(layer)
    if crs is None:
        return result(
            "crs_defined",
            "fail",
            [layer],
            f"{layer.name} has no CRS, so it cannot be placed on Earth or combined with others.",
            details={"crs": None},
            hint="Reload it with crs= if you know the CRS.",
        )
    label = crs_label(crs)
    details = {"crs": label, "crs_source": layer.crs_source}
    if layer.crs_source == "assumed":
        return result(
            "crs_defined",
            "warn",
            [layer],
            f"CRS {label} was assumed by loci, not declared by the source.",
            details=details,
            hint="Confirm the coordinates' datum and reload with crs= to make it explicit.",
        )
    origin = ORIGIN.get(layer.crs_source or "", "")
    return result(
        "crs_defined",
        "pass",
        [layer],
        f"CRS is {label} ({crs.name}){', ' + origin if origin else ''}.",
        details=details,
    )


def check_crs_consistent(layers: Sequence[Layer]) -> CheckResult:
    """fail if inputs to be combined use different CRSs; warn if some have none."""
    crss = [(layer, layer_crs(layer)) for layer in layers]
    missing = [layer.name for layer, crs in crss if crs is None]
    if missing:
        return result(
            "crs_consistent",
            "warn",
            layers,
            f"Cannot compare CRSs: {', '.join(missing)} {'has' if len(missing) == 1 else 'have'} "
            "none.",
            hint="Give every input a CRS first (see crs_defined).",
        )
    groups: list[tuple[CRS, list[str]]] = []
    for layer, crs in crss:
        assert crs is not None
        for known, names in groups:
            if known.equals(crs, ignore_axis_order=True):
                names.append(layer.name)
                break
        else:
            groups.append((crs, [layer.name]))
    details = {"crs": {layer.name: crs_label(crs) for layer, crs in crss if crs is not None}}
    if len(groups) == 1:
        return result(
            "crs_consistent",
            "pass",
            layers,
            f"All {len(layers)} inputs use {crs_label(groups[0][0])}.",
            details=details,
        )
    listing = "; ".join(f"{crs_label(crs)} ({', '.join(names)})" for crs, names in groups)
    return result(
        "crs_consistent",
        "fail",
        layers,
        f"Inputs use {len(groups)} different CRSs: {listing}.",
        details=details,
        hint="Reproject to one CRS as an explicit, recorded step before combining them.",
    )
