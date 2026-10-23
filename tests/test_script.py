"""loci.script in-process, with LOCI_RUN_DIR pointed at a fake run folder."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pystac
import pytest
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci import script
from loci import synthetic as syn
from loci.errors import LociError
from loci.io import Format, detect_format, read
from loci.models import ReadOptions
from loci.tools.load import load
from loci.tools.stac_search import stac_search
from loci.workspace import Workspace


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Workspace) -> Path:
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / script.INPUTS_FILE).write_text("{}")
    (folder / script.PARAMETERS_FILE).write_text(json.dumps({"factor": 2}))
    monkeypatch.setenv(script.RUN_DIR_ENV, str(folder))
    monkeypatch.setenv("LOCI_WORKSPACE", str(ws.root))
    return folder


def _two_bands() -> xr.Dataset:
    red = xr.DataArray(
        np.full((3, 4), 0.25, dtype="float32"),
        coords={"y": [2.5, 1.5, 0.5], "x": [0.5, 1.5, 2.5, 3.5]},
        dims=("y", "x"),
    )
    return xr.Dataset({"red": red, "nir": red * 2}).rio.write_crs(syn.UTM33N)


def test_rasters_save_as_geotiff_with_band_names(run_dir: Path) -> None:
    path = script.save(_two_bands(), "bands.tif")

    ds = read(str(path), Format("raster", "geotiff"), ReadOptions()).data

    assert path.parent == run_dir / script.OUTPUTS_DIR
    assert list(ds.data_vars) == ["red", "nir"]
    assert float(ds["nir"].values.max()) == 0.5
    assert ds.rio.crs.to_epsg() == 32633
    assert np.isnan(ds["red"].rio.nodata)  # float bands default to NaN nodata


def test_a_single_time_step_is_squeezed_but_a_series_is_refused(run_dir: Path) -> None:
    stack = syn.time_stack()

    script.save(stack.isel(time=[0]), "one.tif")
    with pytest.raises(ValueError, match="one time step"):
        script.save(stack, "all.tif")


def test_vectors_tables_and_json_save_by_suffix(run_dir: Path) -> None:
    gpkg = script.save(syn.polygons(), "parcels.gpkg")
    csv = script.save(pd.DataFrame({"month": ["2024-01"], "ndvi": [0.5]}), "series.csv")
    metrics = script.save({"rmse": 0.12}, "metrics.json")

    assert len(read(str(gpkg), detect_format(str(gpkg)), ReadOptions()).data) == syn.POLY_COUNT
    assert pd.read_csv(csv)["ndvi"].tolist() == [0.5]
    assert json.loads(metrics.read_text()) == {"rmse": 0.12}


def test_unsupported_combinations_and_bad_names_are_refused(run_dir: Path) -> None:
    with pytest.raises(TypeError, match=r"Cannot save DataFrame as \.tif"):
        script.save(pd.DataFrame({"a": [1]}), "a.tif")
    with pytest.raises(ValueError, match="plain file names"):
        script.save({"a": 1}, "../escape.json")


def _raw_red() -> xr.DataArray:
    """uint16 reflectance as loaded from STAC: nodata 0, scale 0.0001, offset -0.1."""
    raw = xr.DataArray(
        np.array([[0, 1000], [2000, 3000]], dtype="uint16"),
        coords={"y": [1.5, 0.5], "x": [0.5, 1.5]},
        dims=("y", "x"),
        attrs={"scale_factor": 0.0001, "add_offset": -0.1, "units": "1"},
    )
    return raw.rio.write_crs(syn.UTM33N).rio.write_nodata(0)


def test_physical_masks_nodata_applies_scaling_and_drops_packing_attrs() -> None:
    red = script.physical(_raw_red())

    assert red.dtype == np.float32
    assert np.isnan(red.values[0, 0])
    np.testing.assert_allclose(red.values.ravel()[1:], [0.0, 0.1, 0.2], atol=1e-6)
    assert red.attrs.get("units") == "1"
    assert "scale_factor" not in red.attrs and "add_offset" not in red.attrs
    assert np.isnan(red.rio.nodata)


def test_physical_is_idempotent() -> None:
    once = script.physical(_raw_red())

    np.testing.assert_array_equal(script.physical(once).values, once.values)


def _declare(run_dir: Path, **refs: str) -> None:
    (run_dir / script.INPUTS_FILE).write_text(json.dumps(refs))


def test_read_gives_physical_values_for_scaled_bands(
    run_dir: Path, ws: Workspace, stac_scenes: list[pystac.Item]
) -> None:
    search = stac_search(
        ws, bbox=list(stac_scenes[0].bbox or []), start="2024-03-01", end="2024-03-31"
    )
    _declare(run_dir, stack=load(ws, search.data_ref, bands=["red"]).data_ref)

    red = script.read("stack")["red"]
    stored = script.read("stack", raw=True)["red"]

    assert red.dtype == np.float32 and "scale_factor" not in red.attrs
    expected = [r * syn.STAC_SCALE + syn.STAC_OFFSET for r in syn.STAC_RED]
    np.testing.assert_allclose(red.values[:, 0, 0], expected, atol=1e-6)
    assert stored.dtype == np.uint16 and stored.attrs["scale_factor"] == syn.STAC_SCALE


def test_read_masks_float_nodata_but_keeps_unscaled_integers(
    run_dir: Path, ws: Workspace, ramp_tif: Path, multiband_tif: Path
) -> None:
    _declare(
        run_dir, dem=load(ws, str(ramp_tif)).data_ref, bands=load(ws, str(multiband_tif)).data_ref
    )

    dem = script.read("dem")["band_1"]
    bands = script.read("bands")

    assert int(dem.isnull().sum()) == syn.RAMP_NODATA_ROWS * syn.RAMP_COLS
    assert float(dem.max()) == syn.RAMP_MAX
    assert float(script.read("dem", raw=True)["band_1"].min()) == syn.RAMP_NODATA
    assert bands["red"].dtype == np.uint16  # no scaling declared: could be class codes


def test_float_outputs_never_inherit_scaling(run_dir: Path) -> None:
    raw = _raw_red()
    ratio = raw.astype("float32") / 3000  # xarray keeps the raw band's attrs

    path = script.save(xr.Dataset({"ratio": ratio}), "ratio.tif")

    with rasterio.open(path) as src:
        assert (src.scales, src.offsets) == ((1.0,), (0.0,))
    warning = (run_dir / script.WARNINGS_FILE).read_text()
    assert "ratio.tif: float band(s) ['ratio'] carry scale_factor/add_offset" in warning


def test_integer_outputs_keep_their_scaling(run_dir: Path) -> None:
    path = script.save(xr.Dataset({"red": _raw_red()}), "red.tif")

    with rasterio.open(path) as src:
        assert (src.scales, src.offsets) == ((0.0001,), (-0.1,))
        assert src.nodata == 0
    assert not (run_dir / script.WARNINGS_FILE).exists()


def test_parameters_and_declared_inputs(run_dir: Path, ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref
    (run_dir / script.INPUTS_FILE).write_text(json.dumps({"dem": ref}))

    assert script.parameters() == {"factor": 2}
    assert script.inputs() == {"dem": ref}
    assert list(script.read("dem").data_vars) == ["band_1"]
    with pytest.raises(LociError, match="No input named 'other'"):
        script.read("other")


def test_script_helpers_only_work_inside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(script.RUN_DIR_ENV, raising=False)

    with pytest.raises(LociError, match="only inside a script started by the run tool"):
        script.parameters()
