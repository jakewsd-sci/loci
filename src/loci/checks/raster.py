"""Raster checks: nodata defined, enough valid pixels, and grids that can be combined."""

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from loci.checks.core import Layer, layer_crs, result
from loci.models import CheckResult
from loci.summary.common import iso, sig
from loci.summary.raster import StatsSource, valid_mask

UNIT_SYMBOLS = {"metre": "m", "degree": "°", "US survey foot": "ft", "foot": "ft"}
ALIGNMENT_TOLERANCE = 1e-6  # in pixels
MAX_LISTED = 20


def check_nodata_defined(layer: Layer) -> CheckResult:
    """warn for bands without a nodata value."""
    ds = _raster(layer)
    names = [str(n) for n in ds.data_vars]
    missing = [n for n in names if ds[n].rio.nodata is None]
    details = {"nodata": {n: _float(ds[n].rio.nodata) for n in names[:MAX_LISTED]}}
    if not missing:
        which = f"band '{names[0]}'" if len(names) == 1 else f"all {len(names)} bands"
        return result(
            "nodata_defined", "pass", [layer], f"Nodata is defined for {which}.", details=details
        )
    integer = [n for n in missing if np.issubdtype(ds[n].dtype, np.integer)]
    consequence = (
        "Integer bands then count every stored value as valid, including any fill."
        if integer
        else "Only NaN is treated as missing."
    )
    return result(
        "nodata_defined",
        "warn",
        [layer],
        f"No nodata value for band(s) {missing[:MAX_LISTED]}. {consequence}",
        details=details,
        hint="If the data has fill values, write outputs with a nodata value "
        "(rio.write_nodata); otherwise confirm that every value is real data.",
    )


def check_valid_fraction(layer: Layer, min_fraction: float) -> CheckResult:
    """fail if a band has no valid pixels; warn if a band, or a time step, is below min_fraction."""
    source = layer.stats_source()
    ds = source.ds
    fractions: dict[str, float] = {}
    low_steps: list[tuple[pd.Timestamp, float]] = []
    steps = 0
    for name in (str(n) for n in ds.data_vars):
        valid = valid_mask(np.asarray(ds[name].values), _float(ds[name].rio.nodata))
        fractions[name] = float(valid.mean()) if valid.size else 0.0
        if "time" in ds[name].dims:
            per_step = _per_step(ds[name], valid)
            steps = len(per_step)
            low_steps += [(t, f) for t, f in per_step.items() if f < min_fraction]

    worst_band = min(fractions, key=fractions.__getitem__)
    lowest = fractions[worst_band]
    details = {
        "min_valid_fraction": min_fraction,
        "valid_fraction": {n: sig(f) for n, f in list(fractions.items())[:MAX_LISTED]},
        "method": _method(source),
    }
    if low_steps:
        details["low_steps"] = len({t for t, _ in low_steps})
    approx = "" if source.method == "exact" else f" ({_method(source)})"

    if lowest == 0:
        return result(
            "valid_fraction",
            "fail",
            [layer],
            f"Band '{worst_band}' has no valid pixels{approx}.",
            details=details,
            hint="Check the nodata value and any masking that produced this band.",
        )
    if lowest < min_fraction:
        return result(
            "valid_fraction",
            "warn",
            [layer],
            f"Only {lowest:.0%} of pixels in band '{worst_band}' are valid (threshold "
            f"{min_fraction:.0%}){approx}.",
            details=details,
            hint="Results rest on the valid pixels only. Say how missing pixels are handled, or "
            "choose data with better coverage.",
        )
    if low_steps:
        t, f = min(low_steps, key=lambda step: step[1])
        count = len({step for step, _ in low_steps})
        return result(
            "valid_fraction",
            "warn",
            [layer],
            f"{count} of {steps} time steps have under {min_fraction:.0%} valid pixels (lowest: "
            f"{iso(t)}, {f:.0%}){approx}.",
            details=details,
            hint="Composites and trends over these steps rest on few pixels. Say how they are "
            "handled, for example by dropping or down-weighting them.",
        )
    return result(
        "valid_fraction",
        "pass",
        [layer],
        f"At least {lowest:.0%} of pixels are valid in every band (threshold "
        f"{min_fraction:.0%}){approx}.",
        details=details,
    )


def check_resolution_consistent(layers: Sequence[Layer]) -> CheckResult:
    """warn if rasters to be combined differ in resolution, grid alignment or value units."""
    crss = [layer_crs(layer) for layer in layers]
    first = crss[0]
    if first is None or any(c is None or not c.equals(first, ignore_axis_order=True) for c in crss):
        return result(
            "resolution_consistent",
            "warn",
            layers,
            "Cannot compare grids: the rasters do not share a CRS.",
            hint="Resolve crs_consistent first.",
        )
    unit = UNIT_SYMBOLS.get(first.axis_info[0].unit_name, first.axis_info[0].unit_name)
    grids = {layer.name: _grid(_raster(layer)) for layer in layers}
    details = {"resolution": {n: [sig(g[0]), sig(g[1])] for n, g in grids.items()}, "units": unit}

    findings = []
    resolutions = {(sig(g[0]), sig(g[1])) for g in grids.values()}
    if len(resolutions) > 1:
        listing = ", ".join(f"{n} {g[0]:g} {unit}" for n, g in grids.items())
        findings.append(f"Resolutions differ ({listing}), so combining them needs resampling.")
    else:
        (ref_name, ref), *others = grids.items()
        for name, grid in others:
            dx, dy = _offset(grid, ref)
            if dx or dy:
                findings.append(
                    f"The grid of {name} is offset from {ref_name}'s by {dx:g} {unit} in x and "
                    f"{dy:g} {unit} in y, so combining them needs resampling."
                )
    units = {layer.name: _value_units(_raster(layer)) for layer in layers}
    declared = {n: u for n, u in units.items() if u is not None}
    if len(declared) == len(layers) and len(set(declared.values())) > 1:
        listing = ", ".join(f"{n} in {u}" for n, u in declared.items())
        findings.append(f"Value units differ ({listing}).")
        details["value_units"] = declared

    if findings:
        return result(
            "resolution_consistent",
            "warn",
            layers,
            " ".join(findings),
            details=details,
            hint="Choose the target grid and resampling method explicitly (for example average "
            "for continuous values, mode for classes), convert units, and record both.",
        )
    res = next(iter(resolutions))
    return result(
        "resolution_consistent",
        "pass",
        layers,
        f"All {len(layers)} rasters share an aligned {res[0]:g} {unit} grid.",
        details=details,
    )


def _raster(layer: Layer) -> xr.Dataset:
    if layer.raster is None:
        raise TypeError(f"{layer.name} is not a raster")
    return layer.raster


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _method(source: StatsSource) -> str:
    if source.method == "exact":
        return "every pixel"
    if source.method == "overview":
        return f"from an overview at 1/{source.decimation} resolution"
    return f"from 1 in {source.decimation} pixels along x and y"


def _per_step(da: xr.DataArray, valid: np.ndarray) -> pd.Series:
    """Valid fraction per time step."""
    axes = tuple(i for i, d in enumerate(da.dims) if d != "time")
    return pd.Series(valid.mean(axis=axes), index=pd.DatetimeIndex(da["time"].values))


def _grid(ds: xr.Dataset) -> tuple[float, float, float, float]:
    """(x resolution, y resolution, left edge, top edge)."""
    xres, yres = ds.rio.resolution()
    left, _, _, top = ds.rio.bounds()
    return (abs(float(xres)), abs(float(yres)), float(left), float(top))


def _offset(grid: tuple[float, ...], ref: tuple[float, ...]) -> tuple[float, float]:
    """How far `grid` sits off `ref`'s pixel lattice, in CRS units (0 when aligned)."""
    out = []
    for edge, ref_edge, res in ((grid[2], ref[2], ref[0]), (grid[3], ref[3], ref[1])):
        pixels = (edge - ref_edge) / res
        frac = pixels - round(pixels)
        out.append(0.0 if abs(frac) < ALIGNMENT_TOLERANCE else sig(frac * res) or 0.0)
    return out[0], out[1]


def _value_units(ds: xr.Dataset) -> str | None:
    """The one unit a raster's bands declare, or None if none or several."""
    units = {str(ds[n].attrs["units"]) for n in ds.data_vars if "units" in ds[n].attrs}
    return units.pop() if len(units) == 1 else None
