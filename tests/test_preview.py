"""preview end to end: the PNGs are read back and checked."""

from pathlib import Path
from typing import Any

import numpy as np
import pystac
import pytest
from matplotlib.image import imread

from loci import synthetic as syn
from loci.errors import LociError
from loci.tools.load import load
from loci.tools.preview import preview
from loci.tools.stac_search import stac_search
from loci.workspace import Workspace


def test_single_band_raster_with_transparent_nodata(ws: Workspace, ramp_tif: Path) -> None:
    ref = load(ws, str(ramp_tif)).data_ref

    result = preview(ws, ref)
    image = imread(result.path)

    assert Path(result.path).parent == ws.previews_dir
    assert (result.width, result.height) == (syn.RAMP_COLS, syn.RAMP_ROWS)
    assert image.shape == (syn.RAMP_ROWS, syn.RAMP_COLS, 4)
    assert (image[: syn.RAMP_NODATA_ROWS, :, 3] == 0).all()  # the nodata rows
    assert (image[syn.RAMP_NODATA_ROWS :, :, 3] == 1).all()
    assert result.bands == ["band_1"]
    assert result.shows.startswith("Band band_1 in viridis")


def test_true_colour_is_used_when_rgb_bands_exist(ws: Workspace, tmp_path: Path) -> None:
    path = tmp_path / "rgb.tif"
    values = np.stack([np.full((20, 20), v, dtype="uint16") for v in (300, 600, 900)])
    syn._write_geotiff(
        path, values, crs=syn.UTM33N, res=10.0, nodata=0, descriptions=["red", "green", "blue"]
    )

    result = preview(ws, load(ws, str(path)).data_ref)

    assert result.bands == ["red", "green", "blue"]
    assert result.shows.startswith("True colour (red, green, blue)")


def test_bands_can_be_chosen(ws: Workspace, multiband_tif: Path) -> None:
    ref = load(ws, str(multiband_tif)).data_ref

    assert preview(ws, ref).bands == ["red"]  # no green or blue, so the first band
    assert preview(ws, ref, bands=["nir"]).bands == ["nir"]
    with pytest.raises(LociError, match="one band"):
        preview(ws, ref, bands=["red", "nir"])
    with pytest.raises(LociError, match="Unknown band"):
        preview(ws, ref, bands=["swir"])


def test_large_rasters_are_drawn_from_overviews(ws: Workspace, overview_tif: Path) -> None:
    result = preview(ws, load(ws, str(overview_tif)).data_ref, width=128)

    assert (result.width, result.height) == (128, 128)
    assert "Drawn at 1/4 of the data's resolution." in result.notes


def _stac_stack(ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    items = syn.stac_items(tmp_path / "scenes")

    def fake(**kwargs: Any) -> tuple[list[pystac.Item], int]:
        return items, len(items)

    monkeypatch.setattr("loci.tools.stac_search.search_items", fake)
    search = stac_search(ws, bbox=list(items[0].bbox or []), start="2024-03-01", end="2024-03-31")
    return load(ws, search.data_ref, bands=["red", "nir"]).data_ref


def test_time_stacks_default_to_the_least_cloudy_step(
    ws: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _stac_stack(ws, tmp_path, monkeypatch)

    clearest = preview(ws, ref)
    chosen = preview(ws, ref, time="2024-03-25")

    assert clearest.time == f"{syn.STAC_DATES[syn.STAC_CLEAREST]}T10:00:00"
    assert "the least cloudy of 3 steps (5.0% cloud" in clearest.notes[0]
    assert chosen.time == f"{syn.STAC_DATES[2]}T10:00:00"
    assert "Stretch limits are physical values (stored * scale + offset)." in chosen.notes
    reflectance = syn.STAC_RED[2] * syn.STAC_SCALE + syn.STAC_OFFSET  # each scene is constant
    assert chosen.stretch == pytest.approx((reflectance, reflectance))
    assert f"every valid pixel is {reflectance:.4g}" in chosen.shows


def test_time_needs_a_time_dimension(ws: Workspace, ramp_tif: Path) -> None:
    with pytest.raises(LociError, match="no time dimension"):
        preview(ws, load(ws, str(ramp_tif)).data_ref, time="2024-03-01")


def test_vector_layers_are_drawn_and_can_be_coloured(ws: Workspace, polygons_gpkg: Path) -> None:
    ref = load(ws, str(polygons_gpkg)).data_ref

    plain = preview(ws, ref, width=256)
    coloured = preview(ws, ref, width=256, column="landuse")

    assert (plain.width, plain.height) == (256, 256)
    assert imread(plain.path).shape[:2] == (256, 256)
    assert plain.shows == f"{syn.POLY_COUNT} features to scale on white."
    assert "coloured by 'landuse'" in coloured.shows
    assert coloured.path != plain.path


def test_preview_arguments_must_suit_the_data(
    ws: Workspace, polygons_gpkg: Path, ramp_tif: Path
) -> None:
    vector = load(ws, str(polygons_gpkg)).data_ref
    raster = load(ws, str(ramp_tif)).data_ref

    with pytest.raises(LociError, match="No column"):
        preview(ws, vector, column="colour")
    with pytest.raises(LociError, match="apply to rasters"):
        preview(ws, vector, bands=["red"])
    with pytest.raises(LociError, match="applies to vector"):
        preview(ws, raster, column="landuse")
    with pytest.raises(LociError, match="width"):
        preview(ws, raster, width=4000)
