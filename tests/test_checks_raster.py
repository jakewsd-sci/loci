from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.raster import (
    check_nodata_defined,
    check_resolution_consistent,
    check_valid_fraction,
)
from loci.summary.raster import plan_stats

MakeLayer = Callable[..., Layer]


def _grid(values: np.ndarray, *, nodata: float | None = None, units: str | None = None) -> Layer:
    rows, cols = values.shape
    da = xr.DataArray(
        values,
        coords={
            "y": syn.ORIGIN[1] - 10.0 * (np.arange(rows) + 0.5),
            "x": syn.ORIGIN[0] + 10.0 * (np.arange(cols) + 0.5),
        },
        dims=("y", "x"),
    )
    if nodata is not None:
        da = da.rio.write_nodata(nodata)
    if units is not None:
        da.attrs["units"] = units
    return Layer("grid", xr.Dataset({"b": da}).rio.write_crs(syn.UTM33N))


def test_declared_nodata_passes(make_layer: MakeLayer, multiband_tif: Path) -> None:
    out = check_nodata_defined(make_layer(multiband_tif))

    assert out.status == "pass"
    assert out.message == "Nodata is defined for all 2 bands."
    assert out.details == {"nodata": {"red": 0.0, "nir": 0.0}}


def test_missing_nodata_warns_with_the_consequence(make_layer: MakeLayer, bare_tif: Path) -> None:
    floats = check_nodata_defined(make_layer(bare_tif))
    integers = check_nodata_defined(_grid(np.zeros((3, 3), dtype="uint8")))

    assert floats.status == integers.status == "warn"
    assert "Only NaN is treated as missing" in floats.message
    assert "Integer bands then count every stored value as valid" in integers.message


def test_enough_valid_pixels_passes(make_layer: MakeLayer, ramp_tif: Path) -> None:
    out = check_valid_fraction(make_layer(ramp_tif), min_fraction=0.5)

    assert out.status == "pass"
    assert out.details["valid_fraction"] == {"band_1": syn.RAMP_VALID_FRACTION}
    assert out.details["method"] == "every pixel"


def test_too_few_valid_pixels_warns(make_layer: MakeLayer, ramp_tif: Path) -> None:
    out = check_valid_fraction(make_layer(ramp_tif), min_fraction=0.95)

    assert out.status == "warn"
    assert out.message == "Only 90% of pixels in band 'band_1' are valid (threshold 95%)."


def test_no_valid_pixels_fails() -> None:
    out = check_valid_fraction(_grid(np.full((4, 4), -1.0), nodata=-1.0), min_fraction=0.5)

    assert out.status == "fail"
    assert "no valid pixels" in out.message


def test_cloudy_time_steps_warn() -> None:
    out = check_valid_fraction(Layer("stack", syn.cloudy_time_stack()), min_fraction=0.5)

    assert out.status == "warn"
    assert out.message == (
        f"{len(syn.CLOUDY_MONTHS)} of {syn.TS_STEPS} time steps have under 50% valid pixels "
        "(lowest: 2024-05-15, 20%)."
    )
    assert out.details["low_steps"] == len(syn.CLOUDY_MONTHS)


def test_sampled_valid_fraction_says_so(make_layer: MakeLayer, ramp_tif: Path) -> None:
    layer = make_layer(ramp_tif)
    assert layer.raster is not None
    sampled = Layer(layer.name, layer.data, sample=plan_stats(layer.raster, max_pixels=1000))

    out = check_valid_fraction(sampled, min_fraction=0.5)

    assert out.details["method"] == "from 1 in 3 pixels along x and y"
    assert out.message.endswith("(threshold 50%, from 1 in 3 pixels along x and y).")


def test_matching_grids_pass(make_layer: MakeLayer, ramp_tif: Path, overview_tif: Path) -> None:
    out = check_resolution_consistent([make_layer(ramp_tif, "a"), make_layer(overview_tif, "b")])

    assert out.status == "pass"
    assert out.message == "All 2 rasters share an aligned 10 m grid."


def test_different_resolutions_warn(
    make_layer: MakeLayer, ramp_tif: Path, coarse_tif: Path
) -> None:
    out = check_resolution_consistent([make_layer(ramp_tif, "a"), make_layer(coarse_tif, "b")])

    assert out.status == "warn"
    assert "Resolutions differ (a 10 m, b 20 m)" in out.message
    assert out.hint is not None and "resampling method" in out.hint


def test_misaligned_grids_warn(make_layer: MakeLayer, ramp_tif: Path, shifted_tif: Path) -> None:
    out = check_resolution_consistent([make_layer(ramp_tif, "a"), make_layer(shifted_tif, "b")])

    assert out.status == "warn"
    assert f"offset from a's by {syn.SHIFT_X:g} m in x and 0 m in y" in out.message


def test_different_value_units_warn() -> None:
    kelvin = _grid(np.ones((2, 2)), units="K")
    celsius = Layer("c", _grid(np.ones((2, 2)), units="degC").data)

    out = check_resolution_consistent([kelvin, celsius])

    assert out.status == "warn"
    assert "Value units differ (grid in K, c in degC)" in out.message


def test_grids_in_different_crss_cannot_be_compared(
    make_layer: MakeLayer, ramp_tif: Path, bare_tif: Path
) -> None:
    out = check_resolution_consistent([make_layer(ramp_tif), make_layer(bare_tif)])

    assert out.status == "warn"
    assert "do not share a CRS" in out.message


@pytest.mark.parametrize("min_fraction", [0.0, 0.9])
def test_threshold_is_inclusive(make_layer: MakeLayer, ramp_tif: Path, min_fraction: float) -> None:
    assert check_valid_fraction(make_layer(ramp_tif), min_fraction).status == "pass"


def test_fully_valid_bands_say_all() -> None:
    out = check_valid_fraction(_grid(np.ones((3, 3)), nodata=-1.0), min_fraction=0.5)

    assert out.message == "All pixels are valid in every band (threshold 50%)."
