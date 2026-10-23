import json
import re
from pathlib import Path

import pystac
import pytest

from loci import provenance
from loci.models import SourceInput
from loci.tools.load import load
from loci.tools.stac_search import stac_search
from loci.tools.validate import validate
from loci.workspace import Workspace

HEX64 = re.compile(r"[0-9a-f]{64}")


def test_loading_a_file_records_the_call_options_and_source(
    ws: Workspace, points_lonlat_csv: Path
) -> None:
    ref = load(ws, str(points_lonlat_csv)).data_ref

    record = provenance.read(ws, ref)

    assert record is not None
    assert record.produced_by == "load"
    assert record.call == {"tool": "load", "arguments": {"uri": str(points_lonlat_csv)}}
    assert record.inputs == [SourceInput(uri=str(points_lonlat_csv))]
    assert record.parameters == {"x_col": "lon", "y_col": "lat", "crs": "EPSG:4326"}
    assert record.validation is None


def test_records_pin_the_software(ws: Workspace, ramp_tif: Path) -> None:
    record = provenance.read(ws, load(ws, str(ramp_tif)).data_ref)

    assert record is not None
    assert {"python", "loci", "gdal", "numpy", "xarray", "rasterio"} <= set(record.software)
    assert HEX64.fullmatch(record.software["loci_code_sha256"])


def test_stac_search_and_load_record_catalogue_and_items(
    ws: Workspace, stac_scenes: list[pystac.Item]
) -> None:
    ids = [item.id for item in stac_scenes]
    search = stac_search(
        ws, bbox=list(stac_scenes[0].bbox or []), start="2024-03-01", end="2024-03-31"
    )
    stack = load(ws, search.data_ref, bands=["red"])

    found = provenance.read(ws, search.data_ref)
    loaded = provenance.read(ws, stack.data_ref)

    assert found is not None and loaded is not None
    assert found.produced_by == "stac_search"
    assert found.inputs[0].uri == "https://earth-search.aws.element84.com/v1"
    assert found.inputs[0].stac_items == ids
    assert found.parameters["collection"] == "sentinel-2-l2a"
    assert loaded.inputs[0].data_ref == search.data_ref
    assert loaded.inputs[0].stac_items == ids
    assert loaded.parameters["bands"] == ["red"]
    assert loaded.call["arguments"] == {"uri": search.data_ref, "bands": ["red"]}


def test_validate_keeps_its_latest_report_on_the_record(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    report = validate(ws, [ref])
    record = provenance.read(ws, ref)

    assert record is not None
    assert record.validation == report
    assert record.validated_at is not None


def test_file_records_get_a_sidecar_beside_the_file(ws: Workspace, tmp_path: Path) -> None:
    output = tmp_path / "series.csv"
    output.write_text("month,ndvi\n2024-01,0.5\n")
    record = provenance.new_record(
        produced_by="run", call={"tool": "run", "arguments": {}}, inputs=[], path=str(output)
    )

    provenance.write(ws, record)

    sidecar = json.loads((tmp_path / "series.csv.prov.json").read_text())
    assert sidecar["produced_by"] == "run"
    assert sidecar["path"] == str(output)


def test_missing_records_and_bad_refs(ws: Workspace) -> None:
    assert provenance.read(ws, "raster-00000000") is None
    with pytest.raises(ValueError, match="not a data_ref"):
        provenance.read(ws, "../../etc/passwd")


def test_tool_call_leaves_out_arguments_not_given() -> None:
    call = provenance.tool_call("load", {"uri": "a.tif", "crs": None, "bands": [], "x": 0})

    assert call == {"tool": "load", "arguments": {"uri": "a.tif", "x": 0}}
