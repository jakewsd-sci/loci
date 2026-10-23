"""The `validate` tool: geospatial checks on one or more data_refs, as a pass/warn/fail report."""

import xarray as xr

from loci import provenance
from loci.checks.core import Layer, build_report
from loci.checks.runner import (
    DEFAULT_AREA_TOLERANCE,
    DEFAULT_MIN_VALID_FRACTION,
    CheckOptions,
    cross_checks,
    run_checks,
)
from loci.errors import LociError
from loci.models import ValidationReport
from loci.summary.raster import DEFAULT_MAX_PIXELS, StatsSource
from loci.tools.common import stats_source
from loci.workspace import Workspace


def validate(
    ws: Workspace,
    data_refs: list[str],
    *,
    variables: dict[str, str] | None = None,
    min_valid_fraction: float = DEFAULT_MIN_VALID_FRACTION,
    time_column: str | None = None,
    area_reference: str | None = None,
    area_by: str | None = None,
    area_tolerance: float = DEFAULT_AREA_TOLERANCE,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> ValidationReport:
    """Check one or more data_refs and report pass, warn or fail for each check.

    Every input gets the checks for its kind: CRS defined; for rasters nodata, valid-pixel
    fraction and time sampling; for vectors geometry validity. Several data_refs are taken to be
    combined, so they are also checked against each other: same CRS, overlapping extents and, for
    rasters, the same aligned grid.

    Args:
        variables: Band or column name -> variable (e.g. {"band_1": "reflectance"}) for the
            value-range check. NDVI, NDWI, NDMI, NBR, SAVI and EVI are recognised by name.
        min_valid_fraction: Valid-pixel share below which a band or time step warns.
        time_column: Datetime column of vector inputs whose sampling should be checked.
        area_reference: data_ref of a vector layer whose area the vector inputs must conserve,
            e.g. the input of a dissolve or the zones of an overlay.
        area_by: Column shared by the inputs and the reference; areas are compared per value.
        area_tolerance: Allowed relative area difference (1e-4 is 0.01%).
        max_pixels: Most pixels per band read for raster statistics.

    The report is also kept on each data_ref's provenance record.
    """
    refs = list(dict.fromkeys(data_refs))
    if not refs:
        raise LociError("Pass at least one data_ref to validate.")
    layers = [_layer(ws, ref, max_pixels) for ref in refs]
    options = CheckOptions(
        variables=variables or {},
        min_valid_fraction=min_valid_fraction,
        time_column=time_column,
        area_reference=_layer(ws, area_reference, max_pixels) if area_reference else None,
        area_by=area_by,
        area_tolerance=area_tolerance,
    )
    report = build_report(refs, run_checks(layers, options))
    provenance.record_validation(ws, report)
    return report


def check_together(ws: Workspace, data_refs: list[str]) -> ValidationReport | None:
    """Only the checks of inputs against each other, or None for fewer than two data_refs.

    Not a tool: `run` uses it on its declared inputs. Each input's own checks are left to
    `validate` (and the hook), and the report is not kept on the inputs' provenance records,
    whose validation stays their own.
    """
    refs = list(dict.fromkeys(data_refs))
    if len(refs) < 2:
        return None
    layers = [Layer(ref, ws.open(ref), ws.record(ref).crs_source) for ref in refs]
    return build_report(refs, cross_checks(layers))


def _layer(ws: Workspace, data_ref: str, max_pixels: int) -> Layer:
    """A Layer for a data_ref; for rasters, with its statistics sample read once, up front."""
    record = ws.record(data_ref)
    data = ws.open(data_ref)
    sample = None
    if isinstance(data, xr.Dataset):
        source = stats_source(record, data, max_pixels)
        # compute() returns a loaded copy; load() would make the cached dataset eager in place.
        sample = StatsSource(source.ds.compute(), source.method, source.decimation)
    return Layer(data_ref, data, record.crs_source, sample)
