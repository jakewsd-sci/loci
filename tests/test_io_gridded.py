"""Gridded data (NetCDF, Zarr): read as rasters, with CF axes, a CRS that is declared or flagged,
choices along other dimensions, and the cuts that let a large archive be read in part."""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from loci import synthetic as syn
from loci.errors import LociError
from loci.io import gridded
from loci.tools.export import export
from loci.tools.inspect import inspect
from loci.tools.load import load
from loci.tools.validate import validate
from loci.web import layers
from loci.workspace import Workspace


@pytest.fixture
def climate_nc(tmp_path: Path) -> Path:
    return syn.write_climate_netcdf(tmp_path / "temperature.nc")


def test_a_reanalysis_download_reads_as_a_time_stack_with_its_crs_flagged(
    ws: Workspace, climate_nc: Path
) -> None:
    loaded = load(ws, str(climate_nc))
    data = ws.open(loaded.data_ref)

    assert isinstance(data, xr.Dataset) and data["t2m"].dims == ("time", "y", "x")
    assert loaded.summary.dims == {"time": 240, "y": 9, "x": 9}  # type: ignore[union-attr]
    assert loaded.summary.crs_source == "assumed"
    assert loaded.summary.crs is not None and loaded.summary.crs.epsg == 4326
    assert "names no grid_mapping" in loaded.warnings[0]
    assert "Read valid_time as time, latitude as y, longitude as x." in loaded.notes
    assert np.allclose(data["t2m"].values, syn.climate_dataset()["t2m"].values)  # as stored
    assert validate(ws, [loaded.data_ref]).status == "warn"  # the assumed CRS


def test_bands_carry_units_and_statistics_sample_long_stacks(
    ws: Workspace, climate_nc: Path
) -> None:
    described = inspect(ws, load(ws, str(climate_nc)).data_ref)

    band = described.summary.bands[0]  # type: ignore[union-attr]
    assert (band.units, band.long_name) == ("K", "2 metre temperature")
    assert described.summary.stats_time_step == 10  # type: ignore[union-attr]
    assert any("one time step in every 10" in note for note in described.notes)


def test_other_dimensions_need_a_choice(ws: Workspace, tmp_path: Path) -> None:
    levels = syn.write_climate_netcdf(tmp_path / "levels.nc", levels=True)

    with pytest.raises(LociError, match="pressure_level with 3 values: 1000, 850, 500") as asked:
        load(ws, str(levels))
    chosen = load(ws, str(levels), select={"pressure_level": 850})
    with pytest.raises(LociError, match="has no pressure_level 925") as missing:
        load(ws, str(levels), select={"pressure_level": 925})

    assert asked.value.hint == "Choose one with select={'pressure_level': value}."
    data = ws.open(chosen.data_ref)
    assert isinstance(data, xr.Dataset) and data["t"].dims == ("time", "y", "x")
    assert missing.value.hint is not None and "1000, 850, 500" in missing.value.hint


def test_a_period_and_an_area_are_cut_out_before_anything_is_read(
    ws: Workspace, climate_nc: Path
) -> None:
    cut = load(
        ws,
        str(climate_nc),
        select={"time": ["2010-01-01", "2010-12-31"]},
        bbox=[14.5, 36.5, 15.5, 37.5],
    )

    assert cut.summary.dims == {"time": 12, "y": 5, "x": 5}  # type: ignore[union-attr]
    assert "Selected time from 2010-01-01 to 2010-12-31." in cut.notes


def test_a_long_time_axis_must_be_narrowed(
    ws: Workspace, climate_nc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gridded, "MAX_TIME_STEPS", 100)

    with pytest.raises(LociError, match="holds 240 time steps") as refused:
        load(ws, str(climate_nc))
    narrowed = load(ws, str(climate_nc), select={"time": ["2001-01-01", "2005-12-31"]})

    assert refused.value.hint is not None and 'select={"time"' in refused.value.hint
    assert narrowed.summary.dims["time"] == 60  # type: ignore[union-attr]


def test_a_declared_grid_mapping_gives_the_crs(ws: Workspace, tmp_path: Path) -> None:
    projected = syn.write_projected_netcdf(tmp_path / "projected.nc")

    loaded = load(ws, str(projected))
    with pytest.raises(LociError, match="declares CRS"):
        load(ws, str(projected), crs="EPSG:4326")  # crs= never relabels a declared grid

    assert loaded.summary.crs_source == "file" and loaded.warnings == []
    assert loaded.summary.crs is not None and loaded.summary.crs.epsg == 32633


def test_a_zarr_store_reads_like_the_netcdf_file(ws: Workspace, tmp_path: Path) -> None:
    store = syn.write_climate_zarr(tmp_path / "temperature.zarr")

    loaded = load(ws, str(store))

    data = ws.open(loaded.data_ref)
    assert isinstance(data, xr.Dataset)
    assert np.allclose(data["t2m"].values, syn.climate_dataset()["t2m"].values)
    assert data["t2m"].encoding["preferred_chunks"] == {"time": 240, "y": 9, "x": 9}


def test_longitudes_0_to_360_are_kept_and_shown_from_minus_180(
    ws: Workspace, tmp_path: Path
) -> None:
    wrapped = syn.write_climate_0_360_netcdf(tmp_path / "wrap.nc")

    loaded = load(ws, str(wrapped))
    west = load(ws, str(wrapped), bbox=[-5.0, 36.0, -3.5, 38.0])
    info = layers.layer_info(ws, ws.record(loaded.data_ref))
    point = layers.point_values(ws, loaded.data_ref, -4.0, 37.0)
    with pytest.raises(LociError, match="crosses the 0° meridian"):
        load(ws, str(wrapped), bbox=[-4.0, 36.0, 2.0, 38.0])

    assert any("from 0 to 360 and are kept so" in note for note in loaded.notes)
    assert west.summary.dims["x"] == 7  # type: ignore[union-attr]
    assert info.bounds_wgs84 == (-5.125, 35.875, -2.875, 38.125)
    assert (point.x, len(point.bands[0].values)) == (356.0, 240)


def test_variables_with_different_shapes_and_other_calendars_are_refused(
    ws: Workspace, tmp_path: Path
) -> None:
    mixed = syn.climate_dataset()
    mixed["elevation"] = mixed["t2m"].isel(valid_time=0, drop=True)
    mixed.to_netcdf(tmp_path / "mixed.nc", engine="netcdf4")
    noleap = syn.write_noleap_netcdf(tmp_path / "noleap.nc")

    with pytest.raises(LociError, match="different dimensions") as mixed_error:
        load(ws, str(tmp_path / "mixed.nc"))
    with pytest.raises(LociError, match="uses the noleap calendar"):
        load(ws, str(noleap))

    assert (
        mixed_error.value.hint == "Choose variables that share their dimensions with bands=[...]."
    )
    assert load(ws, str(tmp_path / "mixed.nc"), bands=["elevation"]).summary.dims == {  # type: ignore[union-attr]
        "y": 9,
        "x": 9,
    }


def test_a_point_through_time_that_would_read_too_much_is_refused(
    ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    syn.climate_dataset().to_zarr(
        tmp_path / "hourly.zarr",
        mode="w",
        zarr_format=2,
        consolidated=True,
        encoding={"t2m": {"chunks": (1, 9, 9)}},  # one time step per chunk, as ERA5 is stored
    )
    loaded = load(ws, str(tmp_path / "hourly.zarr"), bbox=[14.5, 36.5, 15.0, 37.0])
    monkeypatch.setitem(layers.POINT_BUDGET, "local", 240 * 9 * 9 * 4 - 1)

    with pytest.raises(LociError, match="stored 9 by 9 cells at a time") as refused:
        layers.point_values(ws, loaded.data_ref, 14.75, 36.75)

    assert refused.value.hint is not None and "shorter period" in refused.value.hint


def test_a_stack_exports_to_netcdf_whole_and_says_its_crs_was_assumed(
    ws: Workspace, climate_nc: Path, tmp_path: Path
) -> None:
    ref = load(ws, str(climate_nc)).data_ref

    result = export(ws, ref, str(tmp_path / "exports" / "temperature.nc"))
    back = load(ws, result.path)

    assert result.format == "netcdf"
    assert "file now states it" in result.notes[0]
    assert back.summary.dims == {"time": 240, "y": 9, "x": 9}  # type: ignore[union-attr]
    assert back.summary.crs_source == "file"
