from pathlib import Path

import numpy as np
import pytest
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci import synthetic as syn
from loci.io import Format, read
from loci.models import ReadOptions
from loci.summary.common import sig
from loci.summary.raster import (
    MAX_BANDS,
    band_stats,
    decimation_for,
    plan_stats,
    raster_notes,
    raster_warnings,
    summarise_raster,
)


def _open(path: Path) -> xr.Dataset:
    data = read(str(path), Format("raster", "geotiff"), ReadOptions()).data
    assert isinstance(data, xr.Dataset)
    return data


def _dataset(values: np.ndarray, *, nodata: float | None = None, names: int = 1) -> xr.Dataset:
    """In-memory raster in UTM 33N with `names` identical bands."""
    rows, cols = values.shape
    coords = {
        "y": syn.ORIGIN[1] - 10.0 * (np.arange(rows) + 0.5),
        "x": syn.ORIGIN[0] + 10.0 * (np.arange(cols) + 0.5),
    }
    bands = {}
    for i in range(names):
        da = xr.DataArray(values, coords=coords, dims=("y", "x"))
        bands[f"b{i}"] = da.rio.write_nodata(nodata) if nodata is not None else da
    return xr.Dataset(bands).rio.write_crs(syn.UTM33N)


def test_exact_stats_match_the_known_answers(ramp_tif: Path) -> None:
    ds = _open(ramp_tif)
    valid = syn.ramp_values()[syn.RAMP_NODATA_ROWS :]
    p2, p98 = np.percentile(valid, [2, 98])

    summary = summarise_raster(ds, stats=plan_stats(ds), crs_source="file")
    stats = summary.bands[0].stats

    assert summary.stats_method == "exact"
    assert summary.stats_decimation == 1
    assert stats is not None
    assert (stats.min, stats.max, stats.mean) == (syn.RAMP_MIN, syn.RAMP_MAX, syn.RAMP_MEAN)
    assert stats.std == sig(syn.RAMP_STD)
    assert (stats.p2, stats.p98) == (sig(p2), sig(p98))
    assert stats.valid_fraction == syn.RAMP_VALID_FRACTION


def test_header_describes_the_grid(ramp_tif: Path) -> None:
    summary = summarise_raster(_open(ramp_tif), crs_source="file")
    top = syn.ORIGIN[1]
    bottom = top - syn.RAMP_ROWS * syn.RAMP_RES
    right = syn.ORIGIN[0] + syn.RAMP_COLS * syn.RAMP_RES

    assert summary.crs is not None and summary.crs.epsg == 32633
    assert summary.crs_source == "file"
    assert summary.extent.bounds == (syn.ORIGIN[0], bottom, right, top)
    assert summary.extent.bounds_wgs84 is not None
    assert summary.resolution == (syn.RAMP_RES, syn.RAMP_RES)
    assert summary.dims == {"y": syn.RAMP_ROWS, "x": syn.RAMP_COLS}
    assert summary.time is None
    band = summary.bands[0]
    assert (band.name, band.dtype, band.nodata) == ("band_1", "float32", syn.RAMP_NODATA)


def test_header_only_summary_reads_no_pixels(ramp_tif: Path) -> None:
    summary = summarise_raster(_open(ramp_tif))

    assert summary.bands[0].stats is None
    assert summary.stats_method is None


def test_large_rasters_are_sampled_and_labelled(ramp_tif: Path) -> None:
    ds = _open(ramp_tif)

    source = plan_stats(ds, max_pixels=1000)
    summary = summarise_raster(ds, stats=source)
    stats = summary.bands[0].stats

    # Step 2 would keep 40 x 50 = 2000 pixels; step 3 keeps 27 x 34 = 918.
    assert (source.method, source.decimation) == ("strided", 3)
    assert summary.stats_method == "strided"
    assert stats is not None
    assert stats.valid_fraction == pytest.approx(syn.RAMP_VALID_FRACTION, abs=0.02)
    assert any("one pixel in every 3" in n for n in raster_notes(summary))


def test_decimation_accounts_for_time_steps() -> None:
    ds = syn.time_stack()  # 12 x 10 x 10

    step = decimation_for(ds, max_pixels=300)

    assert step == 2  # 12 * 5 * 5 = 300


def test_multiband_stats_and_nodata(multiband_tif: Path) -> None:
    ds = _open(multiband_tif)

    summary = summarise_raster(ds, stats=plan_stats(ds))

    assert [b.name for b in summary.bands] == ["red", "nir"]
    red, nir = (b.stats for b in summary.bands)
    assert red is not None and nir is not None
    assert (red.mean, nir.mean) == (syn.MB_RED, syn.MB_NIR)
    assert red.valid_fraction == syn.MB_VALID_FRACTION
    assert raster_warnings(summary) == []


def test_time_coverage_and_stats_across_time() -> None:
    ds = syn.time_stack()

    summary = summarise_raster(ds, stats=plan_stats(ds))

    assert summary.dims == {"time": syn.TS_STEPS, "y": syn.TS_SIZE, "x": syn.TS_SIZE}
    assert list(summary.dims) == ["time", "y", "x"]
    assert summary.time is not None
    assert (summary.time.start, summary.time.end) == (syn.TS_START, syn.TS_END)
    assert summary.time.steps == syn.TS_STEPS
    assert (
        summary.time.min_step_days,
        summary.time.median_step_days,
        summary.time.max_step_days,
    ) == (syn.TS_MIN_STEP_DAYS, syn.TS_MEDIAN_STEP_DAYS, syn.TS_MAX_STEP_DAYS)
    stats = summary.bands[0].stats
    assert stats is not None
    assert stats.mean == pytest.approx(0.05 * 6.5)


def test_missing_nodata_and_empty_bands_are_warned(bare_tif: Path) -> None:
    bare = _open(bare_tif)
    empty = _dataset(np.full((4, 4), -1.0), nodata=-1.0)

    bare_summary = summarise_raster(bare, stats=plan_stats(bare))
    empty_summary = summarise_raster(empty, stats=plan_stats(empty))

    assert any("No nodata value" in w for w in raster_warnings(bare_summary))
    assert any("no valid pixels" in w for w in raster_warnings(empty_summary))


def test_scale_and_offset_are_reported_with_a_note() -> None:
    ds = _dataset(np.ones((4, 4)), nodata=0.0)
    ds["b0"].attrs.update(scale_factor=0.0001, add_offset=-0.1)

    summary = summarise_raster(ds)

    assert (summary.bands[0].scale, summary.bands[0].offset) == (0.0001, -0.1)
    assert any("scale" in n for n in raster_notes(summary))


def test_band_list_is_capped() -> None:
    ds = _dataset(np.ones((2, 2)), nodata=0.0, names=MAX_BANDS + 5)

    summary = summarise_raster(ds)

    assert len(summary.bands) == MAX_BANDS
    assert summary.bands_omitted == 5
    assert any("5 more bands" in n for n in raster_notes(summary))


def test_band_stats_treat_nan_and_nodata_as_invalid() -> None:
    values = np.array([[1.0, 2.0], [np.nan, -9999.0]])

    with_nodata = band_stats(values, -9999.0)
    nan_nodata = band_stats(values, float("nan"))

    assert (with_nodata.min, with_nodata.max, with_nodata.valid_fraction) == (1.0, 2.0, 0.5)
    assert nan_nodata.valid_fraction == 0.75
    assert nan_nodata.min == -9999.0


def test_band_stats_with_no_valid_pixels() -> None:
    stats = band_stats(np.zeros((3, 3), dtype="uint16"), 0)

    assert stats.valid_fraction == 0.0
    assert stats.mean is None
