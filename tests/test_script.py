"""loci.script in-process, with LOCI_RUN_DIR pointed at a fake run folder."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from loci import script
from loci import synthetic as syn
from loci.errors import LociError
from loci.io import Format, detect_format, read
from loci.models import ReadOptions
from loci.tools.load import load
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
