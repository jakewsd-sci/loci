"""run end to end: real subprocesses with the project's Python."""

import hashlib
import json
from pathlib import Path

import pystac
import pytest
import xarray as xr

from loci import provenance
from loci import synthetic as syn
from loci.errors import LociError, UnknownRefError
from loci.tools.load import load
from loci.tools.run import run
from loci.tools.stac_search import stac_search
from loci.workspace import Workspace

DOUBLE = """
import xarray as xr
from loci import script

dem = script.read("dem")["band_1"]
factor = script.parameters().get("factor", 2)
doubled = xr.where(dem == dem.rio.nodata, dem, dem * factor).rio.write_nodata(dem.rio.nodata)
script.save(xr.Dataset({"elevation": doubled}), "doubled.tif")
print("doubled with factor", factor)
"""


def test_a_script_reads_inputs_and_its_output_is_registered(ws: Workspace, ramp_tif: Path) -> None:
    dem = load(ws, str(ramp_tif)).data_ref

    result = run(ws, code=DOUBLE, inputs={"dem": dem})
    output = result.outputs[0]

    assert result.status == "ok" and result.exit_code == 0
    assert "doubled with factor 2" in result.stdout
    assert (output.file, output.kind, output.validation) == ("doubled.tif", "raster", "pass")
    assert output.data_ref is not None
    doubled = ws.open(output.data_ref)
    assert isinstance(doubled, xr.Dataset)
    assert float(doubled["elevation"].max()) == 2 * syn.RAMP_MAX
    assert Path(result.run_dir, "run.json").exists()


def test_outputs_carry_provenance_and_a_sidecar(ws: Workspace, ramp_tif: Path) -> None:
    dem = load(ws, str(ramp_tif)).data_ref

    result = run(ws, code=DOUBLE, inputs={"dem": dem}, parameters={"factor": 3})
    ref = result.outputs[0].data_ref
    assert ref is not None
    record = provenance.read(ws, ref)

    assert record is not None
    assert record.produced_by == "run"
    assert record.run_id == result.run_id
    assert record.code_sha256 == hashlib.sha256(DOUBLE.encode()).hexdigest()
    assert Path(record.script or "").read_text() == DOUBLE
    assert record.inputs[0].name == "dem" and record.inputs[0].data_ref == dem
    assert record.parameters == {"factor": 3}
    assert record.validation is not None and record.validation.status == "pass"
    sidecar = json.loads(Path(f"{record.path}{provenance.SIDECAR_SUFFIX}").read_text())
    assert sidecar["sha256"] == record.sha256
    assert sidecar["validation"]["status"] == "pass"


def test_non_spatial_outputs_are_kept_as_files(ws: Workspace) -> None:
    code = (
        "import pandas as pd\nfrom loci import script\n"
        "script.save(pd.DataFrame({'month': ['2024-01'], 'ndvi': [0.5]}), 'series.csv')\n"
    )

    result = run(ws, code=code)
    output = result.outputs[0]

    assert output.data_ref is None
    assert output.note is not None and output.note.startswith("Kept as a file")
    assert Path(result.run_dir, "outputs", "series.csv.prov.json").exists()


def test_a_failing_script_registers_nothing(ws: Workspace) -> None:
    code = "from loci import script\nscript.save({'partial': True}, 'partial.json')\n1 / 0\n"

    result = run(ws, code=code)

    assert (result.status, result.exit_code) == ("error", 1)
    assert "ZeroDivisionError" in result.stderr
    assert result.outputs[0].data_ref is None
    assert result.outputs[0].note == "Not registered: the run did not finish successfully."
    assert result.warnings == ["The script failed with exit code 1; see stderr."]


def test_a_slow_script_is_stopped(ws: Workspace) -> None:
    result = run(ws, code="import time\nprint('started', flush=True)\ntime.sleep(30)\n", timeout=2)

    assert result.status == "timeout"
    assert result.exit_code is None
    assert result.seconds < 20
    assert result.warnings == ["The script was stopped after 2 s."]


def test_only_declared_inputs_can_be_read(ws: Workspace, ramp_tif: Path) -> None:
    dem = load(ws, str(ramp_tif)).data_ref

    result = run(ws, code="from loci import script\nscript.read('other')\n", inputs={"dem": dem})

    assert result.status == "error"
    assert "No input named 'other'" in result.stderr


def test_scripts_can_be_files(ws: Workspace, ramp_tif: Path, tmp_path: Path) -> None:
    path = tmp_path / "double.py"
    path.write_text(DOUBLE)

    result = run(ws, path=str(path), inputs={"dem": load(ws, str(ramp_tif)).data_ref})
    ref = result.outputs[0].data_ref
    assert ref is not None
    record = provenance.read(ws, ref)

    assert result.status == "ok"
    assert record is not None
    arguments = record.call["arguments"]
    assert isinstance(arguments, dict) and arguments["path"] == str(path)


def test_outputs_that_fail_validation_are_flagged(ws: Workspace) -> None:
    code = (
        "import numpy as np, xarray as xr\nfrom loci import script\n"
        "da = xr.DataArray(np.ones((2, 2), 'float32'), coords={'y': [1.5, 0.5], 'x': [0.5, 1.5]},"
        " dims=('y', 'x'))\n"
        "script.save(xr.Dataset({'v': da}), 'no_crs.tif')\n"
    )

    result = run(ws, code=code)

    assert result.outputs[0].validation == "fail"
    assert any("has no CRS" in p for p in result.outputs[0].problems)
    assert result.warnings == ["Output(s) ['no_crs.tif'] failed validation; see their problems."]


def test_run_arguments_are_checked_before_anything_runs(ws: Workspace) -> None:
    with pytest.raises(LociError, match="code= or path="):
        run(ws)
    with pytest.raises(LociError, match="code= or path="):
        run(ws, code="pass", path="x.py")
    with pytest.raises(UnknownRefError):
        run(ws, code="pass", inputs={"dem": "raster-00000000"})
    with pytest.raises(LociError, match="timeout"):
        run(ws, code="pass", timeout=0)
    with pytest.raises(LociError, match="JSON"):
        run(ws, code="pass", parameters={"when": object()})
    assert not list(ws.runs_dir.iterdir())


def _ndvi_code(convert: str) -> str:
    return (
        "from loci import script\n"
        "stack = script.read('stack')\n"
        f"red, nir = {convert}(stack['red']), {convert}(stack['nir'])\n"
        "script.save(((nir - red) / (nir + red)).median('time').rename('ndvi'), 'ndvi.tif')\n"
    )


def test_ndvi_from_physical_values_is_right_and_raw_ratios_are_flagged(
    ws: Workspace, stac_scenes: list[pystac.Item]
) -> None:
    search = stac_search(
        ws, bbox=list(stac_scenes[0].bbox or []), start="2024-03-01", end="2024-03-31"
    )
    stack = load(ws, search.data_ref, bands=["red", "nir"]).data_ref

    physical = run(ws, code=_ndvi_code("script.physical"), inputs={"stack": stack})
    raw = run(ws, code=_ndvi_code(""), inputs={"stack": stack})

    def ndvi(result_ref: str | None) -> float:
        assert result_ref is not None
        opened = ws.open(result_ref)
        assert isinstance(opened, xr.Dataset)
        return float(opened["ndvi"].mean())

    # Physical red on the three scenes is 0.00, 0.01 and 0.02 and nir is 0.2, so the median NDVI
    # is 0.19 / 0.21. Raw digital numbers ignore the offset and give 1900 / 4100 instead.
    assert ndvi(physical.outputs[0].data_ref) == pytest.approx(0.19 / 0.21, abs=1e-6)
    assert not any("scale_factor" in w for w in physical.warnings)
    assert ndvi(raw.outputs[0].data_ref) == pytest.approx(1900 / 4100, abs=1e-6)
    assert any("ndvi.tif: float band(s) ['ndvi']" in w for w in raw.warnings)
