"""Which checks apply to which inputs.

Every input gets the checks for its kind. Several inputs are taken to be combined, so they are
also checked against each other. Area conservation runs when a reference layer is given.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from loci.checks.core import Layer
from loci.checks.crs import check_crs_consistent, check_crs_defined
from loci.checks.extent import check_extents_overlap
from loci.checks.geometry import check_area_conserved, check_geometry_valid
from loci.checks.raster import (
    check_nodata_defined,
    check_resolution_consistent,
    check_valid_fraction,
)
from loci.checks.temporal import check_temporal_coverage
from loci.checks.values import VARIABLES, check_band_range, check_column_range, resolve_variable
from loci.errors import LociError
from loci.models import CheckResult

DEFAULT_MIN_VALID_FRACTION = 0.5
DEFAULT_AREA_TOLERANCE = 1e-4


@dataclass(frozen=True)
class CheckOptions:
    """Settings for a run. `variables` maps band or column names to known variable names."""

    variables: Mapping[str, str] = field(default_factory=dict)
    min_valid_fraction: float = DEFAULT_MIN_VALID_FRACTION
    time_column: str | None = None
    area_reference: Layer | None = None
    area_by: str | None = None
    area_tolerance: float = DEFAULT_AREA_TOLERANCE


def run_checks(layers: Sequence[Layer], options: CheckOptions) -> list[CheckResult]:
    """Per-input checks in input order, then checks across inputs, then area conservation."""
    check_options(layers, options)
    results = [r for layer in layers for r in _layer_checks(layer, options)]
    if len(layers) > 1:
        results += [check_crs_consistent(layers), check_extents_overlap(layers)]
        rasters = [layer for layer in layers if layer.raster is not None]
        if len(rasters) > 1:
            results.append(check_resolution_consistent(rasters))
    if options.area_reference is not None:
        results += [
            check_area_conserved(
                layer,
                options.area_reference,
                by=options.area_by,
                tolerance=options.area_tolerance,
            )
            for layer in layers
            if layer.vector is not None
        ]
    return results


def check_options(layers: Sequence[Layer], options: CheckOptions) -> None:
    """Reject settings that cannot apply, so a typo never passes as a clean report."""
    unknown = sorted(set(options.variables.values()) - set(VARIABLES))
    if unknown:
        raise LociError(f"Unknown variable(s) {unknown}.", hint=f"Known: {', '.join(VARIABLES)}.")
    names = {name for layer in layers for name in _names(layer)}
    unmatched = sorted(set(options.variables) - names)
    if unmatched:
        raise LociError(
            f"variables= names {unmatched} match no band or column of the inputs.",
            hint=f"Bands and columns: {', '.join(sorted(names)[:20])}.",
        )
    if not 0 <= options.min_valid_fraction <= 1:
        raise LociError("min_valid_fraction must be between 0 and 1.")
    vectors = [layer for layer in layers if layer.vector is not None]
    if options.time_column and not any(options.time_column in v.data.columns for v in vectors):
        raise LociError(f"No vector input has a column {options.time_column!r}.")
    reference = options.area_reference
    if reference is not None:
        if reference.vector is None:
            raise LociError("area_reference must be a vector layer.")
        if not vectors:
            raise LociError("Area conservation needs at least one vector input.")
        if options.area_by is not None:
            lacking = [
                layer.name
                for layer in [*vectors, reference]
                if options.area_by not in layer.data.columns
            ]
            if lacking:
                raise LociError(f"Column {options.area_by!r} is missing from {lacking}.")
    elif options.area_by is not None:
        raise LociError("area_by= needs area_reference=.")
    if options.area_tolerance < 0:
        raise LociError("area_tolerance must not be negative.")


def _layer_checks(layer: Layer, options: CheckOptions) -> list[CheckResult]:
    results = [check_crs_defined(layer)]
    if layer.raster is not None:
        ds = layer.raster
        results += [
            check_nodata_defined(layer),
            check_valid_fraction(layer, options.min_valid_fraction),
        ]
        for band in (str(b) for b in ds.data_vars):
            if (found := resolve_variable(band, options.variables)) is not None:
                variable, inferred = found
                results.append(check_band_range(layer, band, variable, inferred=inferred))
        if "time" in ds.dims:
            results.append(check_temporal_coverage(layer, ds["time"].values))
        return results

    gdf = layer.data
    results.append(check_geometry_valid(layer))
    for column in (str(c) for c in gdf.columns if c != gdf.geometry.name):
        if (found := resolve_variable(column, options.variables)) is not None:
            variable, inferred = found
            results.append(check_column_range(layer, column, variable, inferred=inferred))
    if options.time_column and options.time_column in gdf.columns:
        results.append(check_temporal_coverage(layer, gdf[options.time_column]))
    return results


def _names(layer: Layer) -> list[str]:
    if layer.raster is not None:
        return [str(b) for b in layer.raster.data_vars]
    return [str(c) for c in layer.data.columns if c != layer.data.geometry.name]
