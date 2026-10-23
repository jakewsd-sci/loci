"""Check the fixtures' known answers with rasterio and geopandas directly, not loci's readers."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import shapely

from loci import synthetic as syn


def test_ramp_raster_matches_known_answers(ramp_tif: Path) -> None:
    with rasterio.open(ramp_tif) as src:
        values = src.read(1)
        assert src.crs.to_epsg() == 32633
        assert src.nodata == syn.RAMP_NODATA
        assert src.res == (syn.RAMP_RES, syn.RAMP_RES)

    valid = values[values != syn.RAMP_NODATA]
    assert valid.size / values.size == pytest.approx(syn.RAMP_VALID_FRACTION)
    assert (valid.min(), valid.max()) == (syn.RAMP_MIN, syn.RAMP_MAX)
    assert valid.mean() == pytest.approx(syn.RAMP_MEAN)
    assert valid.std() == pytest.approx(syn.RAMP_STD)


def test_multiband_raster_matches_known_answers(multiband_tif: Path) -> None:
    with rasterio.open(multiband_tif) as src:
        red, nir = src.read(1).astype(float), src.read(2).astype(float)
        assert src.descriptions == ("red", "nir")

    valid = red != syn.MB_NODATA
    assert valid.mean() == pytest.approx(syn.MB_VALID_FRACTION)
    ndvi = (nir[valid] - red[valid]) / (nir[valid] + red[valid])
    assert np.allclose(ndvi, syn.MB_NDVI)


def test_bare_raster_has_no_crs_or_nodata(bare_tif: Path) -> None:
    with rasterio.open(bare_tif) as src:
        assert src.crs is None
        assert src.nodata is None


def test_overview_raster_has_overviews(overview_tif: Path) -> None:
    with rasterio.open(overview_tif) as src:
        assert tuple(src.overviews(1)) == syn.OV_FACTORS
        assert src.read(1).mean() == pytest.approx(syn.OV_MEAN)


def test_time_stack_matches_known_answers() -> None:
    ds = syn.time_stack()
    times = pd.DatetimeIndex(ds["time"].values)
    steps = np.diff(times).astype("timedelta64[D]").astype(float)

    assert ds.rio.crs.to_epsg() == 32633
    assert (str(times[0].date()), str(times[-1].date())) == (syn.TS_START, syn.TS_END)
    assert len(times) == syn.TS_STEPS
    assert (steps.min(), np.median(steps), steps.max()) == (
        syn.TS_MIN_STEP_DAYS,
        syn.TS_MEDIAN_STEP_DAYS,
        syn.TS_MAX_STEP_DAYS,
    )


def test_polygons_match_known_answers(polygons_path: Path) -> None:
    if polygons_path.suffix == ".parquet":
        gdf = gpd.read_parquet(polygons_path)
    else:
        gdf = gpd.read_file(polygons_path)

    assert len(gdf) == syn.POLY_COUNT
    assert gdf.to_crs(syn.UTM33N).area.to_numpy() == pytest.approx(syn.POLY_AREA)
    assert gdf["score"].mean() == pytest.approx(syn.POLY_SCORE_MEAN)
    assert sorted(gdf["landuse"]) == sorted(syn.POLY_LANDUSE)


def test_dirty_polygons_have_one_of_each_problem(dirty_gpkg: Path) -> None:
    geoms = gpd.read_file(dirty_gpkg).geometry.to_numpy()

    assert shapely.is_missing(geoms).tolist() == [False, False, False, True]
    assert shapely.is_empty(geoms).tolist() == [False, False, True, False]
    assert not shapely.is_valid(geoms[syn.DIRTY_INVALID_ROW])


def test_point_tables_match_known_answers(
    points_lonlat_csv: Path, points_xy_csv: Path, points_blank_csv: Path, wide_csv: Path
) -> None:
    lonlat = pd.read_csv(points_lonlat_csv)
    assert len(lonlat) == syn.POINTS_LONLAT_COUNT
    assert not lonlat.duplicated(["lon", "lat"]).any()

    assert len(pd.read_csv(points_xy_csv)) == syn.POINTS_XY_COUNT

    blank = pd.read_csv(points_blank_csv)
    coords = blank[["lon", "lat"]].apply(pd.to_numeric, errors="coerce")
    assert len(blank) == syn.POINTS_BLANK_ROWS
    assert coords.isna().any(axis=1).sum() == syn.POINTS_BLANK_INVALID

    wide = pd.read_csv(wide_csv)
    assert wide.shape[1] == 2 + syn.WIDE_NUMERIC_COLUMNS + syn.WIDE_TEXT_COLUMNS
