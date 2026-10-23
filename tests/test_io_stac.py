from pathlib import Path

import numpy as np
import pystac
import pytest

from loci import synthetic as syn
from loci.errors import LoadError
from loci.io import STAC_ITEMS, STAC_RASTER, Format, read
from loci.io.stac import items_frame, loadable_bands, read_items, save_items
from loci.models import ReadOptions


@pytest.fixture
def items(tmp_path: Path) -> list[pystac.Item]:
    return syn.stac_items(tmp_path / "scenes")


@pytest.fixture
def items_file(items: list[pystac.Item], tmp_path: Path) -> str:
    path = tmp_path / "items.json"
    save_items(items, path, {"bbox": list(items[0].bbox or []), "collection": "synthetic"})
    return str(path)


def test_items_become_a_frame_of_footprints(items: list[pystac.Item]) -> None:
    frame = items_frame(items)

    assert list(frame["id"]) == [f"SYN_{d.replace('-', '')}" for d in syn.STAC_DATES]
    assert list(frame["cloud_cover"]) == list(syn.STAC_CLOUD)
    assert str(frame["datetime"].iloc[0]) == f"{syn.STAC_DATES[0]} 10:00:00"
    assert list(frame["epsg"]) == [32633] * 3
    assert frame.crs.to_epsg() == 4326
    assert frame.geometry.iloc[0].contains(frame.geometry.iloc[0].centroid)


def test_no_items_make_an_empty_frame_with_the_same_columns() -> None:
    frame = items_frame([])

    assert len(frame) == 0
    assert {"id", "datetime", "cloud_cover", "grid", "epsg"} <= set(frame.columns)


def test_items_round_trip_with_their_search(items: list[pystac.Item], items_file: str) -> None:
    restored, search = read_items(items_file)

    assert [i.id for i in restored] == [i.id for i in items]
    assert search["collection"] == "synthetic"


def test_search_results_read_as_a_vector_layer(items_file: str) -> None:
    result = read(items_file, STAC_ITEMS, ReadOptions())

    assert result.kind == "vector"
    assert len(result.data) == len(syn.STAC_DATES)
    assert result.crs_source == "file"


def test_loadable_bands_are_single_band_geotiffs(items: list[pystac.Item]) -> None:
    assert loadable_bands(items[0]) == ["red", "nir", "scl"]


def test_bands_load_as_a_time_stack_with_scale_and_offset(items_file: str) -> None:
    result = read(items_file, STAC_RASTER, ReadOptions(bands=["red", "nir"]))
    ds = result.data

    assert dict(ds.sizes) == {"time": 3, "y": syn.STAC_SIZE, "x": syn.STAC_SIZE}
    assert list(ds["red"].values[:, 0, 0]) == list(syn.STAC_RED)
    assert ds["red"].attrs["scale_factor"] == syn.STAC_SCALE
    assert ds["red"].attrs["add_offset"] == syn.STAC_OFFSET
    assert ds["red"].rio.nodata == 0
    assert result.options.resolution == syn.STAC_RES
    assert result.options.output_crs == "EPSG:32633"
    assert result.crs_source == "file"
    assert result.warnings == [] and result.notes == []


def test_reopening_with_resolved_options_gives_the_same_data(items_file: str) -> None:
    first = read(items_file, STAC_RASTER, ReadOptions(bands=["red"]))

    again = read(items_file, STAC_RASTER, first.options)

    assert again.data.identical(first.data)


def test_bands_must_be_chosen_from_those_available(items_file: str) -> None:
    with pytest.raises(LoadError, match="Choose the bands") as missing:
        read(items_file, STAC_RASTER, ReadOptions())
    with pytest.raises(LoadError, match="Unknown band"):
        read(items_file, STAC_RASTER, ReadOptions(bands=["swir"]))

    assert missing.value.hint == "Bands: red, nir, scl."


def test_mixed_resolutions_need_an_explicit_choice(items_file: str) -> None:
    with pytest.raises(LoadError, match="different native resolutions"):
        read(items_file, STAC_RASTER, ReadOptions(bands=["red", "scl"]))

    result = read(items_file, STAC_RASTER, ReadOptions(bands=["red", "scl"], resolution=20))

    assert result.data.rio.resolution() == (20.0, -20.0)
    assert result.notes == ["Resampled to 20 from native (red 10) with nearest."]


def test_reprojection_needs_a_resolution_and_is_noted(items_file: str) -> None:
    with pytest.raises(LoadError, match="explicit resolution"):
        read(items_file, STAC_RASTER, ReadOptions(bands=["red"], output_crs="EPSG:32634"))

    result = read(
        items_file, STAC_RASTER, ReadOptions(bands=["red"], output_crs="EPSG:32634", resolution=10)
    )

    assert result.data.rio.crs.to_epsg() == 32634
    assert result.crs_source == "argument"
    assert result.notes == ["3 of 3 items reprojected to EPSG:32634 at 10 with nearest."]


def test_disagreeing_offsets_are_left_unapplied_with_a_warning(tmp_path: Path) -> None:
    items = syn.stac_items(tmp_path / "scenes", offsets=[0.0, -0.1, -0.1])
    save_items(items, tmp_path / "items.json", {})

    result = read(str(tmp_path / "items.json"), STAC_RASTER, ReadOptions(bands=["red"]))

    assert "add_offset" not in result.data["red"].attrs
    assert "different scale/offset for band 'red'" in result.warnings[0]


def test_options_must_suit_the_source(items_file: str, ramp_tif: Path) -> None:
    with pytest.raises(LoadError, match="output_crs="):
        read(items_file, STAC_RASTER, ReadOptions(bands=["red"], crs="EPSG:4326"))
    with pytest.raises(LoadError, match="apply only to STAC items"):
        read(str(ramp_tif), Format("raster", "geotiff"), ReadOptions(bands=["red"]))
    with pytest.raises(LoadError, match="no read options"):
        read(items_file, STAC_ITEMS, ReadOptions(bands=["red"]))


def test_empty_search_cannot_be_loaded(tmp_path: Path) -> None:
    save_items([], tmp_path / "none.json", {})

    with pytest.raises(LoadError, match="found no items"):
        read(str(tmp_path / "none.json"), STAC_RASTER, ReadOptions(bands=["red"]))


def test_stac_values_are_physical_after_scaling(items_file: str) -> None:
    ds = read(items_file, STAC_RASTER, ReadOptions(bands=["red"])).data
    red = ds["red"]

    physical = red.values * red.attrs["scale_factor"] + red.attrs["add_offset"]

    assert np.allclose(
        physical[:, 0, 0], [r * syn.STAC_SCALE + syn.STAC_OFFSET for r in syn.STAC_RED]
    )
