"""Summaries of rasters held as xarray Datasets (one variable per band).

Statistics never read more than `max_pixels` values per band. Larger rasters are summarised from
an overview or a strided sample, and the summary says which, so the numbers can be weighed.
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci.models import (
    BandStats,
    BandSummary,
    CrsSource,
    RasterSummary,
    StatsMethod,
    TimeCoverage,
)
from loci.summary.common import counted, crs_info, extent, iso, sig, take_within_budget

DEFAULT_MAX_PIXELS = 4_000_000
MAX_BANDS = 20
MAX_TIME_SAMPLE = 24  # time steps read for statistics; longer stacks are sampled evenly


@dataclass(frozen=True)
class StatsSource:
    """The data statistics are computed from, and how it relates to the full raster.

    `decimation` is the step between sampled pixels along x and y (1 for every pixel), and
    `time_step` the step between sampled time steps: a long stack such as an hourly reanalysis is
    stored a step or a few at a time, so reading every step would read all of it.
    """

    ds: xr.Dataset
    method: StatsMethod
    decimation: int
    time_step: int = 1


def pixels_per_band(ds: xr.Dataset) -> int:
    first = next(iter(ds.data_vars.values()), None)
    return math.prod(first.shape) if first is not None else 0


def decimation_for(ds: xr.Dataset, max_pixels: int) -> int:
    """Smallest step along x and y that keeps a strided sample within max_pixels per band."""
    rows, cols = ds.sizes["y"], ds.sizes["x"]
    other = pixels_per_band(ds) // max(rows * cols, 1)  # e.g. the number of time steps
    step = max(1, math.isqrt(pixels_per_band(ds) // max_pixels))
    while step < max(rows, cols) and (
        other * math.ceil(rows / step) * math.ceil(cols / step) > max_pixels
    ):
        step += 1
    return step


def plan_stats(ds: xr.Dataset, max_pixels: int = DEFAULT_MAX_PIXELS) -> StatsSource:
    """Every pixel if the raster is small enough, otherwise a strided sample, of at most
    MAX_TIME_SAMPLE evenly spaced time steps."""
    time_step = 1
    if "time" in ds.dims and ds.sizes["time"] > MAX_TIME_SAMPLE:
        time_step = math.ceil(ds.sizes["time"] / MAX_TIME_SAMPLE)
        ds = ds.isel(time=slice(None, None, time_step))
    step = decimation_for(ds, max_pixels)
    if step == 1:
        return StatsSource(ds, "exact" if time_step == 1 else "strided", 1, time_step)
    sample = ds.isel(x=slice(None, None, step), y=slice(None, None, step))
    return StatsSource(sample, "strided", step, time_step)


def valid_mask(values: np.ndarray, nodata: float | None) -> np.ndarray:
    """True where a pixel is valid: finite and not equal to nodata."""
    valid = np.isfinite(values)
    if nodata is not None and not math.isnan(nodata):
        valid &= values != nodata
    return valid


def band_stats(values: np.ndarray, nodata: float | None) -> BandStats:
    """Statistics over valid pixels: finite and not equal to nodata."""
    valid = valid_mask(values, nodata)
    count = int(valid.sum())
    fraction = sig(count / values.size) if values.size else 0.0
    if count == 0:
        return BandStats(
            min=None, max=None, mean=None, std=None, p2=None, p98=None, valid_fraction=0.0
        )
    v = values[valid].astype("float64")
    p2, p98 = np.percentile(v, [2, 98])
    return BandStats(
        min=sig(v.min()),
        max=sig(v.max()),
        mean=sig(v.mean()),
        std=sig(v.std()),
        p2=sig(p2),
        p98=sig(p98),
        valid_fraction=fraction if fraction is not None else 0.0,
    )


def time_coverage(ds: xr.Dataset) -> TimeCoverage | None:
    if "time" not in ds.dims:
        return None
    times = pd.DatetimeIndex(ds["time"].values).sort_values()
    days = np.diff(times.to_numpy()).astype("timedelta64[s]").astype(float) / 86_400
    return TimeCoverage(
        start=iso(times[0]),
        end=iso(times[-1]),
        steps=len(times),
        min_step_days=sig(days.min()) if days.size else None,
        median_step_days=sig(np.median(days)) if days.size else None,
        max_step_days=sig(days.max()) if days.size else None,
    )


def summarise_raster(
    ds: xr.Dataset, *, stats: StatsSource | None = None, crs_source: CrsSource | None = None
) -> RasterSummary:
    """Header facts for every raster; per-band statistics only when `stats` is given."""
    names = [str(n) for n in ds.data_vars]
    bands = take_within_budget(
        (_band_summary(ds[n], stats.ds[n] if stats else None) for n in names),
        max_items=MAX_BANDS,
    )
    xres, yres = ds.rio.resolution()
    return RasterSummary(
        crs=crs_info(ds.rio.crs),
        crs_source=crs_source,
        extent=extent(ds.rio.bounds(), ds.rio.crs),
        resolution=(sig(abs(xres)) or 0.0, sig(abs(yres)) or 0.0),
        dims=_dims(ds),
        time=time_coverage(ds),
        bands=bands,
        bands_omitted=len(names) - len(bands),
        stats_method=stats.method if stats else None,
        stats_decimation=stats.decimation if stats else None,
        stats_time_step=stats.time_step if stats and stats.time_step > 1 else None,
    )


def raster_warnings(summary: RasterSummary) -> list[str]:
    """Problems likely to bias or break analysis. (A missing CRS is reported by the reader.)"""
    warnings = []
    no_nodata = [b.name for b in summary.bands if b.nodata is None]
    if no_nodata:
        warnings.append(
            f"No nodata value for band(s) {no_nodata}: every stored value except NaN counts "
            "as valid, including any fill around the data."
        )
    empty = [b.name for b in summary.bands if b.stats is not None and b.stats.valid_fraction == 0]
    if empty:
        warnings.append(f"Band(s) {empty} have no valid pixels.")
    return warnings


def raster_notes(summary: RasterSummary) -> list[str]:
    """Context needed to read the summary correctly."""
    notes = []
    if summary.stats_method == "overview":
        notes.append(
            f"Statistics come from overviews at 1/{summary.stats_decimation} resolution, so "
            "extremes and valid fractions are approximate."
        )
    elif summary.stats_method == "strided" and (summary.stats_decimation or 1) > 1:
        notes.append(
            f"Statistics come from one pixel in every {summary.stats_decimation} along x and y, "
            "so extremes and valid fractions are approximate."
        )
    if summary.stats_time_step:
        notes.append(
            f"Statistics come from one time step in every {summary.stats_time_step}, so they "
            "describe the period roughly."
        )
    if any(b.scale is not None or b.offset is not None for b in summary.bands):
        notes.append(
            "Values are as stored. Physical value = stored * scale + offset for bands that "
            "declare them."
        )
    if summary.bands_omitted:
        notes.append(
            f"{counted(summary.bands_omitted, 'more band is', 'more bands are')} not listed; "
            f"summaries show the first {len(summary.bands)}."
        )
    return notes


def _band_summary(da: xr.DataArray, sample: xr.DataArray | None) -> BandSummary:
    nodata = _float_or_none(da.rio.nodata)
    scale = _float_or_none(da.attrs.get("scale_factor"))
    offset = _float_or_none(da.attrs.get("add_offset"))
    units, long_name = da.attrs.get("units"), da.attrs.get("long_name")
    return BandSummary(
        name=str(da.name),
        dtype=str(da.dtype),
        nodata=nodata,
        scale=None if scale in (None, 1.0) else sig(scale),
        offset=None if offset in (None, 0.0) else sig(offset),
        units=str(units)[:40] if units else None,
        long_name=str(long_name)[:80] if long_name else None,
        stats=band_stats(np.asarray(sample.values), nodata) if sample is not None else None,
    )


def _dims(ds: xr.Dataset) -> dict[str, int]:
    """Dimension sizes in the bands' own order, e.g. time, y, x."""
    first = next(iter(ds.data_vars.values()), None)
    order = first.dims if first is not None else tuple(ds.sizes)
    return {str(d): int(ds.sizes[d]) for d in order}


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)
