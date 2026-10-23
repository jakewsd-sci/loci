"""Check the fixtures' known answers with rasterio and geopandas directly, not loci's readers."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
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


def test_cloudy_stack_has_known_valid_fractions() -> None:
    ds = syn.cloudy_time_stack()
    valid = np.isfinite(ds["ndvi"].values).mean(axis=(1, 2))
    months = pd.DatetimeIndex(ds["time"].values).month

    for month, fraction in zip(months, valid, strict=True):
        expected = syn.CLOUDY_VALID_FRACTION if month in syn.CLOUDY_MONTHS else 1.0
        assert fraction == pytest.approx(expected)


def test_gappy_stack_has_known_gaps() -> None:
    times = pd.DatetimeIndex(syn.gappy_time_stack()["time"].values)
    steps = np.diff(times).astype("timedelta64[D]").astype(float)

    assert sorted(steps[steps > 45]) == list(syn.GAPPY_GAP_DAYS)


def test_ndvi_rasters_match_known_answers(ndvi_tif: Path, bad_ndvi_tif: Path) -> None:
    with rasterio.open(ndvi_tif) as good, rasterio.open(bad_ndvi_tif) as bad:
        assert good.descriptions == ("ndvi",)
        assert (good.read(1).min(), good.read(1).max()) == pytest.approx(
            (syn.NDVI_MIN, syn.NDVI_MAX)
        )
        assert bad.read(1).max() == pytest.approx(syn.NDVI_BAD)


def test_grid_variants_match_known_answers(coarse_tif: Path, shifted_tif: Path) -> None:
    with rasterio.open(coarse_tif) as coarse, rasterio.open(shifted_tif) as shifted:
        assert coarse.res == (syn.COARSE_RES, syn.COARSE_RES)
        assert coarse.bounds.right == syn.ORIGIN[0] + syn.RAMP_COLS * syn.RAMP_RES
        assert shifted.bounds.left == syn.ORIGIN[0] + syn.SHIFT_X


def test_cloudy_stac_scenes_cover_their_top_rows(tmp_path: Path) -> None:
    items = syn.stac_items(tmp_path, red=(1500, 1600, 1700), cloud_rows=(12, 0, 24))

    with rasterio.open(items[2].assets["red"].href) as src:
        red = src.read(1)
    with rasterio.open(items[2].assets["scl"].href) as src:
        scl = src.read(1)

    assert (red[:24] == syn.CLOUD_DN).all() and (red[24:] == 1700).all()
    assert (scl[:12] == syn.SCL_CLOUD).all() and (scl[12:] == syn.SCL_CLEAR).all()
    with pytest.raises(ValueError, match="even"):
        syn.stac_items(tmp_path / "odd", cloud_rows=(3, 0, 0))


def test_catchment_dem_means_survive_its_nodata(tmp_path: Path) -> None:
    with rasterio.open(syn.write_catchment_dem(tmp_path / "dem.tif")) as src:
        values = np.ma.masked_equal(src.read(1), syn.DEM_NODATA)
    half = syn.DEM_SIZE // 2
    catchments = gpd.read_file(syn.write_catchments(tmp_path / "catchments.geojson"))

    assert values[:, :half].mean() == syn.DEM_WEST_MEAN
    assert values[:, half:].mean() == syn.DEM_EAST_MEAN
    assert values.mask.sum() == syn.DEM_NODATA_ROWS * half
    assert catchments.crs.to_epsg() == 4326
    assert catchments.to_crs(syn.UTM33N).area.round(3).tolist() == [syn.CATCHMENT_AREA] * 2


def test_sample_sites_hit_known_ndvi_pixels(tmp_path: Path, ndvi_tif: Path) -> None:
    sites = pd.read_csv(syn.write_sample_sites_csv(tmp_path / "sites.csv"))
    with rasterio.open(ndvi_tif) as src:
        inside = [
            (x, y)
            for x, y in zip(sites["easting"], sites["northing"], strict=True)
            if src.bounds.left < x < src.bounds.right and src.bounds.bottom < y < src.bounds.top
        ]
        values = [v[0] for v in src.sample(inside)]

    assert len(sites) - len(inside) == syn.SAMPLE_OUTSIDE
    assert np.mean(values) == pytest.approx(syn.SAMPLE_NDVI_MEAN, abs=1e-6)


def test_two_slope_dem_is_60_percent_steep_in_metres_and_all_steep_in_degrees(
    tmp_path: Path,
) -> None:
    with rasterio.open(syn.write_two_slope_dem(tmp_path / "terrain.tif")) as src:
        heights = src.read(1).astype("float64")
        assert src.crs.to_epsg() == 4326
    middle = syn.SLOPE_ORIGIN[1] - syn.SLOPE_SIZE * syn.SLOPE_RES_DEG / 2
    geod = pyproj.Geod(ellps="WGS84")
    lon0 = syn.SLOPE_ORIGIN[0]
    dx = geod.line_length([lon0, lon0 + syn.SLOPE_RES_DEG], [middle, middle])
    dy = geod.line_length([lon0, lon0], [middle, middle + syn.SLOPE_RES_DEG])

    def steep_percent(dy: float, dx: float) -> float:
        rise_y, rise_x = np.gradient(heights, dy, dx)
        slope = np.degrees(np.arctan(np.hypot(rise_x, rise_y)))
        return float((slope > 15).mean() * 100)

    assert steep_percent(dy, dx) == syn.SLOPE_STEEP_PERCENT
    assert steep_percent(syn.SLOPE_RES_DEG, syn.SLOPE_RES_DEG) == 100.0  # degrees as metres
