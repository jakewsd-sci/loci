from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci import synthetic as syn
from loci.checks.core import Layer
from loci.checks.values import (
    VARIABLES,
    check_band_range,
    check_column_range,
    check_value_range,
    resolve_variable,
)

MakeLayer = Callable[..., Layer]


def test_declared_variables_win_and_clear_names_are_inferred() -> None:
    assert resolve_variable("band_1", {"band_1": "reflectance"}) == (
        VARIABLES["reflectance"],
        False,
    )
    assert resolve_variable("NDVI", {}) == (VARIABLES["ndvi"], True)
    assert resolve_variable("slope", {}) is None  # degrees or percent? not guessed


def test_ndvi_within_range_passes(make_layer: MakeLayer, ndvi_tif: Path) -> None:
    out = check_band_range(make_layer(ndvi_tif), "ndvi", VARIABLES["ndvi"], inferred=True)

    assert out.status == "pass"
    assert out.message == (
        "NDVI values in band 'ndvi' span -0.2 to 0.8, within [-1, 1] "
        "(variable inferred from the name)."
    )


def test_ndvi_out_of_range_fails(make_layer: MakeLayer, bad_ndvi_tif: Path) -> None:
    out = check_band_range(make_layer(bad_ndvi_tif), "ndvi", VARIABLES["ndvi"], inferred=False)

    assert out.status == "fail"
    assert "reach 1.2, outside [-1, 1]" in out.message
    assert out.details["max"] == syn.NDVI_BAD


def _reflectance(stored: float, *, scaled: bool) -> Layer:
    da = xr.DataArray(
        np.full((2, 2), stored, dtype="uint16"),
        coords={"y": [1.5, 0.5], "x": [0.5, 1.5]},
        dims=("y", "x"),
    ).rio.write_nodata(0)
    if scaled:
        da.attrs.update(scale_factor=0.0001, add_offset=-0.1)
    return Layer("s2", xr.Dataset({"red": da}).rio.write_crs(syn.UTM33N))


def test_band_range_uses_scale_and_offset() -> None:
    scaled = check_band_range(
        _reflectance(2000, scaled=True), "red", VARIABLES["reflectance"], inferred=False
    )
    raw = check_band_range(
        _reflectance(2000, scaled=False), "red", VARIABLES["reflectance"], inferred=False
    )

    assert scaled.status == "pass"
    assert scaled.details["max"] == 0.1  # 2000 * 0.0001 - 0.1
    assert raw.status == "warn"  # reflectance is a plausibility range, so outside only warns
    assert "reach 2000" in raw.message


def test_column_range_checks_attribute_values() -> None:
    parcels = Layer("parcels", syn.polygons())

    as_percent = check_column_range(parcels, "score", VARIABLES["percent"], inferred=False)
    as_fraction = check_column_range(parcels, "score", VARIABLES["fraction"], inferred=False)
    text = check_column_range(parcels, "landuse", VARIABLES["fraction"], inferred=False)

    assert as_percent.status == "pass"
    assert as_fraction.status == "fail"
    assert "reach 3.5, outside [0, 1]" in as_fraction.message
    assert text.status == "fail"
    assert "not numbers" in text.message


def test_open_ended_ranges_and_missing_values() -> None:
    layer = Layer("flow", syn.polygons().assign(acc=pd.Series([0.0, 5.0, 1e9, 2.0])))

    unbounded = check_column_range(layer, "acc", VARIABLES["flow_accumulation"], inferred=False)
    empty = check_value_range(layer, "acc", VARIABLES["ndvi"], None, None, inferred=False)

    assert unbounded.status == "pass"
    assert unbounded.details["range"] == [0, None]
    assert "within [0, ∞]" in unbounded.message
    assert empty.status == "warn"
