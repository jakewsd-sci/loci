"""lineage back to original sources, through load, stac_search and run."""

import os
from pathlib import Path

import pystac
import pytest

from loci import provenance
from loci.errors import LociError, UnknownRefError
from loci.tools.lineage import lineage
from loci.tools.load import load
from loci.tools.run import run
from loci.tools.stac_search import stac_search
from loci.tools.validate import validate
from loci.workspace import Workspace

DOUBLE = """
from loci import script
dem = script.read("dem")
script.save(dem * 2, "doubled.tif")
"""

MEDIAN = """
from loci import script
stack = script.read("stack")
script.save(stack.median("time"), "median.tif")
"""


def test_a_loaded_file_traces_to_itself(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    result = lineage(ws, ref)

    assert [s.produced_by for s in result.steps] == ["load"]
    assert result.steps[0].description == f"load of {ramp_tif}."
    assert result.original_sources == [str(ramp_tif)]
    assert result.stac_item_count == 0
    assert result.record_path == str(provenance.path_for(ws, ref))


def test_a_run_output_traces_through_its_inputs(ws: Workspace, ramp_tif: Path) -> None:
    dem = load(ws, str(ramp_tif)).data_ref
    output = run(ws, code=DOUBLE, inputs={"dem": dem}).outputs[0].data_ref
    assert output is not None

    result = lineage(ws, output)

    assert [(s.data_ref, s.produced_by) for s in result.steps] == [(output, "run"), (dem, "load")]
    assert result.steps[0].inputs == [dem]
    assert result.steps[0].validation == "pass"  # run validates its outputs
    assert result.original_sources == [str(ramp_tif)]
    assert result.provenance is not None
    assert result.provenance.validation is None  # kept out of the compact copy
    assert result.provenance.code_sha256 is not None


def test_a_stac_analysis_traces_to_the_catalogue_and_its_items(
    ws: Workspace, stac_scenes: list[pystac.Item]
) -> None:
    search = stac_search(
        ws, bbox=list(stac_scenes[0].bbox or []), start="2024-03-01", end="2024-03-31"
    )
    stack = load(ws, search.data_ref, bands=["red"]).data_ref
    median = run(ws, code=MEDIAN, inputs={"stack": stack}).outputs[0].data_ref
    assert median is not None

    result = lineage(ws, median)

    assert [s.produced_by for s in result.steps] == ["run", "load", "stac_search"]
    assert result.steps[1].description == (
        f"load of bands ['red'] from 3 STAC items found by {search.data_ref}."
    )
    assert result.steps[2].description.startswith("stac_search of sentinel-2-l2a in https://")
    assert result.original_sources == ["https://earth-search.aws.element84.com/v1"]
    assert result.stac_items == [item.id for item in stac_scenes]
    assert result.stac_item_count == 3


def test_files_written_by_a_run_trace_through_their_sidecar(ws: Workspace, ramp_tif: Path) -> None:
    dem = load(ws, str(ramp_tif)).data_ref
    code = (
        "import pandas as pd\nfrom loci import script\n"
        "dem = script.read('dem')\n"
        "script.save(pd.DataFrame({'max': [float(dem['band_1'].max())]}), 'stats.csv')\n"
    )
    result = run(ws, code=code, inputs={"dem": dem})
    stats = Path(result.run_dir, "outputs", "stats.csv")

    traced = lineage(ws, str(stats))

    assert traced.file == str(stats) and traced.data_ref is None
    assert [s.produced_by for s in traced.steps] == ["run", "load"]
    assert traced.original_sources == [str(ramp_tif)]


def test_validation_status_shows_on_each_step(ws: Workspace, bare_tif: Path) -> None:
    ref = load(ws, str(bare_tif)).data_ref
    assert lineage(ws, ref).steps[0].validation is None

    validate(ws, [ref])

    assert lineage(ws, ref).steps[0].validation == "fail"  # no CRS


def test_changed_sources_and_missing_records_are_reported(
    ws: Workspace, ramp_tif: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "dem.tif"
    copy.write_bytes(ramp_tif.read_bytes())
    ref = load(ws, str(copy)).data_ref
    stat = copy.stat()
    os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    provenance.path_for(ws, ref).unlink()

    result = lineage(ws, ref)

    assert result.steps[0].produced_by is None
    assert result.steps[0].sources == [str(copy)]
    assert any("has changed since it was loaded" in w for w in result.warnings)
    assert any("predates provenance" in n for n in result.notes)


def test_unknown_targets_are_errors(ws: Workspace, tmp_path: Path) -> None:
    with pytest.raises(UnknownRefError):
        lineage(ws, "raster-00000000")
    with pytest.raises(LociError, match="provenance sidecar"):
        lineage(ws, str(tmp_path / "nothing.csv"))
