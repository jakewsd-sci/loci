"""Exports: data written as they are held, with notes, a provenance sidecar and lineage."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio

from loci import provenance
from loci import synthetic as syn
from loci.errors import LociError
from loci.io.write import write_raster
from loci.tools.export import export
from loci.tools.lineage import lineage
from loci.tools.load import load
from loci.workspace import Workspace


def test_a_raster_is_written_as_it_is_held_and_traces_back(
    ws: Workspace, ramp_tif: Path, tmp_path: Path
) -> None:
    ref = load(ws, str(ramp_tif)).data_ref
    target = tmp_path / "exports" / "ramp.tif"

    result = export(ws, ref, str(target))

    with rasterio.open(ramp_tif) as original, rasterio.open(target) as written:
        assert written.crs == original.crs and written.transform == original.transform
        assert written.nodata == original.nodata and written.dtypes == original.dtypes
        assert np.array_equal(written.read(), original.read())
    assert (result.format, result.notes, result.bytes) == ("geotiff", [], target.stat().st_size)
    traced = lineage(ws, str(target))
    assert [s.produced_by for s in traced.steps] == ["export", "load"]
    assert traced.steps[0].description == f"export of {ref} as geotiff."
    assert traced.original_sources == [str(ramp_tif)]
    sidecar = Path(f"{target}{provenance.SIDECAR_SUFFIX}")
    assert sidecar.exists() and provenance.sha256_file(target) in sidecar.read_text("utf-8")


def test_band_names_and_packing_survive(ws: Workspace, multiband_tif: Path, tmp_path: Path) -> None:
    ref = load(ws, str(multiband_tif)).data_ref

    export(ws, ref, str(tmp_path / "bands.tif"))

    with rasterio.open(multiband_tif) as original, rasterio.open(tmp_path / "bands.tif") as out:
        assert out.descriptions == ("red", "nir")
        assert (out.nodata, out.dtypes) == (original.nodata, original.dtypes)
        assert np.array_equal(out.read(), original.read())


def test_a_time_stack_is_written_one_step_at_a_time(tmp_path: Path) -> None:
    stack = syn.time_stack()

    with pytest.raises(LociError, match="time steps, and a GeoTIFF holds one") as no_step:
        write_raster(stack, tmp_path / "all.tif")
    notes = write_raster(stack, tmp_path / "march.tif", step=2)

    with rasterio.open(tmp_path / "march.tif") as written:
        assert np.allclose(written.read(1), 0.05 * 3)  # the 15th of month m holds 0.05 * m
    assert no_step.value.hint is not None and "step to export" in no_step.value.hint
    assert notes == [f"Wrote the time step of {str(stack.time.values[2])[:10]} (step 2)."]


def test_bands_of_different_types_are_written_as_one_type_with_a_note(tmp_path: Path) -> None:
    stack = syn.time_stack().isel(time=[0])
    stack["count"] = stack["ndvi"].astype("uint8")

    notes = write_raster(stack, tmp_path / "mixed.tif", step=0)

    with rasterio.open(tmp_path / "mixed.tif") as written:
        assert written.dtypes == ("float32", "float32")
    assert any("one data type: wrote float32 (from float32, uint8)" in n for n in notes)


def test_vectors_go_to_geopackage_geojson_or_csv_with_notes(
    ws: Workspace, polygons_gpkg: Path, points_lonlat_csv: Path, tmp_path: Path
) -> None:
    polygons = load(ws, str(polygons_gpkg)).data_ref
    points = load(ws, str(points_lonlat_csv)).data_ref

    package = export(ws, polygons, str(tmp_path / "parcels.gpkg"))
    geojson = export(ws, polygons, str(tmp_path / "parcels.geojson"))
    table = export(ws, polygons, str(tmp_path / "parcels.csv"))
    sites = export(ws, points, str(tmp_path / "sites.csv"))

    original = gpd.read_file(polygons_gpkg)
    assert gpd.read_file(tmp_path / "parcels.gpkg").geometry.equals(original.geometry)
    assert package.notes == []
    assert "meant to be in WGS 84; this file keeps EPSG:32633" in geojson.notes[0]
    assert pd.read_csv(tmp_path / "parcels.csv").columns[0] == "wkt"
    assert table.notes == ["CSV holds no CRS: the wkt column is in EPSG:32633."]
    # A point table keeps its own coordinate columns, which hold the geometry already.
    assert list(pd.read_csv(tmp_path / "sites.csv").columns) == ["site", "lon", "lat", "value"]
    assert sites.notes == ["CSV holds no CRS: the columns lon and lat are in EPSG:4326."]


def test_a_format_that_cannot_hold_the_data_is_refused(
    ws: Workspace, ramp_tif: Path, tmp_path: Path
) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    with pytest.raises(LociError, match=r"A raster cannot be exported as \.gpkg") as refused:
        export(ws, ref, str(tmp_path / "ramp.gpkg"))

    assert refused.value.hint is not None and "geotiff (.tif)" in refused.value.hint
    assert [p.name for p in tmp_path.iterdir()] == [".loci"]  # nothing left behind
