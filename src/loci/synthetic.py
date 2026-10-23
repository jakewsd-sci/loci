"""Synthetic datasets with exactly known answers.

Used by the test suite and, from M6, by the benchmark. Each writer is deterministic, writes one
small file and returns its path. The constants beside each writer are the answers tests check.
All projected fixtures use WGS 84 / UTM zone 33N with their upper-left corner at ORIGIN.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

UTM33N = "EPSG:32633"
ORIGIN = (500_000.0, 4_100_000.0)

# Ramp raster: value = column index; the first rows are nodata.
RAMP_ROWS, RAMP_COLS = 80, 100
RAMP_RES = 10.0
RAMP_NODATA = -9999.0
RAMP_NODATA_ROWS = 8
RAMP_VALID_FRACTION = 0.9
RAMP_MIN, RAMP_MAX, RAMP_MEAN = 0.0, 99.0, 49.5
RAMP_STD = float(np.sqrt((RAMP_COLS**2 - 1) / 12))  # population std of 0..99

# Two-band reflectance raster: constant red and nir, a nodata block in the top-left corner.
MB_SIZE = 50
MB_RED, MB_NIR = 1000, 3000
MB_NODATA = 0
MB_NODATA_BLOCK = 10
MB_VALID_FRACTION = 0.96
MB_NDVI = 0.5

# Raster with no CRS and no nodata.
BARE_SIZE = 20

# Tiled raster with internal overviews: value = column index.
OV_SIZE = 512
OV_BLOCK = 256
OV_FACTORS = (2, 4, 8)
OV_MEAN = 255.5

# Monthly time stack (in memory): one NDVI value per month, the 15th of each month of 2024.
TS_SIZE = 10
TS_STEPS = 12
TS_START, TS_END = "2024-01-15", "2024-12-15"
TS_MIN_STEP_DAYS, TS_MEDIAN_STEP_DAYS, TS_MAX_STEP_DAYS = 29.0, 31.0, 31.0

# Clean polygons: a 2 x 2 grid of 100 m squares.
POLY_SIZE = 100.0
POLY_AREA = POLY_SIZE**2
POLY_COUNT = 4
POLY_LANDUSE = ["forest", "urban", "forest", "water"]
POLY_SCORE = [0.5, 1.5, 2.5, 3.5]
POLY_SCORE_MEAN = 2.0
POLY_SURVEYED = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]

# Dirty polygons: valid, self-intersecting bowtie, empty, null (in that row order).
DIRTY_INVALID_ROW = 1

# Point tables.
POINTS_LONLAT_COUNT = 20
POINTS_LONLAT_ORIGIN = (15.0, 37.0)
POINTS_LONLAT_STEP = 0.01
POINTS_XY_COUNT = 10
POINTS_BLANK_ROWS = 5
POINTS_BLANK_INVALID = 2
WIDE_NUMERIC_COLUMNS = 60
WIDE_TEXT_COLUMNS = 5


def _write_geotiff(
    path: Path,
    bands: np.ndarray,
    *,
    crs: str | None,
    res: float,
    nodata: float | None,
    descriptions: list[str] | None = None,
    block: int | None = None,
    overviews: tuple[int, ...] = (),
) -> Path:
    count, rows, cols = bands.shape
    profile: dict[str, object] = {
        "driver": "GTiff",
        "width": cols,
        "height": rows,
        "count": count,
        "dtype": bands.dtype.name,
        "crs": crs,
        "transform": from_origin(*ORIGIN, res, res),
        "nodata": nodata,
    }
    if block is not None:
        profile.update(tiled=True, blockxsize=block, blockysize=block)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(bands)
        for i, description in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, description)
        if overviews:
            dst.build_overviews(list(overviews), Resampling.average)
    return path


def ramp_values() -> np.ndarray:
    """The ramp raster's pixel values, nodata included."""
    values = np.tile(np.arange(RAMP_COLS, dtype="float32"), (RAMP_ROWS, 1))
    values[:RAMP_NODATA_ROWS] = RAMP_NODATA
    return values


def write_ramp_raster(path: Path) -> Path:
    """Single-band float32, 10 m pixels, value = column index, 10% nodata."""
    return _write_geotiff(path, ramp_values()[None], crs=UTM33N, res=RAMP_RES, nodata=RAMP_NODATA)


def write_multiband_raster(path: Path) -> Path:
    """Two uint16 bands described as 'red' and 'nir'; NDVI is MB_NDVI wherever valid."""
    red = np.full((MB_SIZE, MB_SIZE), MB_RED, dtype="uint16")
    nir = np.full((MB_SIZE, MB_SIZE), MB_NIR, dtype="uint16")
    for band in (red, nir):
        band[:MB_NODATA_BLOCK, :MB_NODATA_BLOCK] = MB_NODATA
    return _write_geotiff(
        path,
        np.stack([red, nir]),
        crs=UTM33N,
        res=10.0,
        nodata=MB_NODATA,
        descriptions=["red", "nir"],
    )


def write_bare_raster(path: Path) -> Path:
    """Single-band float32 of ones with no CRS and no nodata value."""
    ones = np.ones((1, BARE_SIZE, BARE_SIZE), dtype="float32")
    return _write_geotiff(path, ones, crs=None, res=1.0, nodata=None)


def write_overview_raster(path: Path) -> Path:
    """Tiled float32 with average-resampled overviews; value = column index."""
    values = np.tile(np.arange(OV_SIZE, dtype="float32"), (OV_SIZE, 1))[None]
    return _write_geotiff(
        path,
        values,
        crs=UTM33N,
        res=10.0,
        nodata=-9999.0,
        block=OV_BLOCK,
        overviews=OV_FACTORS,
    )


def time_stack() -> xr.Dataset:
    """Monthly NDVI stack with dims (time, y, x); step i holds the value 0.05 * (i + 1)."""
    times = pd.date_range("2024-01-01", periods=TS_STEPS, freq="MS") + pd.Timedelta(days=14)
    x = ORIGIN[0] + 10.0 * (np.arange(TS_SIZE) + 0.5)
    y = ORIGIN[1] - 10.0 * (np.arange(TS_SIZE) + 0.5)
    values = (0.05 * (np.arange(TS_STEPS) + 1))[:, None, None] * np.ones((TS_SIZE, TS_SIZE))
    ndvi = xr.DataArray(
        values.astype("float32"),
        coords={"time": times, "y": y, "x": x},
        dims=("time", "y", "x"),
    )
    return xr.Dataset({"ndvi": ndvi}).rio.write_crs(UTM33N)


def polygons() -> gpd.GeoDataFrame:
    """The clean 2 x 2 polygon grid, row-major from the top-left square."""
    x0, y0 = ORIGIN
    squares = [
        box(
            x0 + c * POLY_SIZE,
            y0 - (r + 1) * POLY_SIZE,
            x0 + (c + 1) * POLY_SIZE,
            y0 - r * POLY_SIZE,
        )
        for r in range(2)
        for c in range(2)
    ]
    return gpd.GeoDataFrame(
        {
            "id": list(range(1, POLY_COUNT + 1)),
            "landuse": POLY_LANDUSE,
            "score": POLY_SCORE,
            "surveyed": pd.to_datetime(POLY_SURVEYED),
        },
        geometry=squares,
        crs=UTM33N,
    )


def write_polygons(path: Path) -> Path:
    """Write the clean polygons; the format follows the suffix (.gpkg, .geojson, .shp, .parquet).

    GeoJSON is written in WGS 84 as RFC 7946 requires. Shapefiles store dates without times.
    """
    gdf = polygons()
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        gdf.to_parquet(path)
    elif suffix == ".geojson":
        gdf.to_crs("EPSG:4326").to_file(path)
    elif suffix == ".shp":
        gdf.assign(surveyed=gdf["surveyed"].dt.date).to_file(path)
    else:
        gdf.to_file(path)
    return path


def write_dirty_polygons(path: Path) -> Path:
    """Four rows: a valid square, a self-intersecting bowtie, an empty polygon and a null."""
    x0, y0 = ORIGIN
    bowtie = Polygon([(x0, y0), (x0 + 10, y0 + 10), (x0 + 10, y0), (x0, y0 + 10)])
    geometries = [box(x0, y0 - 10, x0 + 10, y0), bowtie, Polygon(), None]
    gdf = gpd.GeoDataFrame({"id": [1, 2, 3, 4]}, geometry=geometries, crs=UTM33N)
    gdf.to_file(path)
    return path


def points_lonlat() -> pd.DataFrame:
    """A 5 x 4 grid of field sites in lon/lat with a value column 0..19."""
    i = np.arange(POINTS_LONLAT_COUNT)
    lon0, lat0 = POINTS_LONLAT_ORIGIN
    return pd.DataFrame(
        {
            "site": [f"S{k:02d}" for k in i + 1],
            "lon": np.round(lon0 + POINTS_LONLAT_STEP * (i % 5), 6),
            "lat": np.round(lat0 + POINTS_LONLAT_STEP * (i // 5), 6),
            "value": i.astype(float),
        }
    )


def write_points_lonlat_csv(path: Path) -> Path:
    points_lonlat().to_csv(path, index=False)
    return path


def write_points_xy_csv(path: Path) -> Path:
    """Ten points with easting/northing columns named x and y, in UTM 33N (CRS not stated)."""
    i = np.arange(POINTS_XY_COUNT)
    df = pd.DataFrame({"x": ORIGIN[0] + 100.0 * i, "y": ORIGIN[1] - 50.0 * i, "value": i})
    df.to_csv(path, index=False)
    return path


def write_points_blank_csv(path: Path) -> Path:
    """Five lon/lat rows, two of which have a missing or non-numeric coordinate."""
    path.write_text(
        "site,lon,lat,value\n"
        "A,15.00,37.00,1\n"
        "B,,37.01,2\n"
        "C,15.02,n/a,3\n"
        "D,15.03,37.03,4\n"
        "E,15.04,37.04,5\n"
    )
    return path


def write_wide_csv(path: Path) -> Path:
    """Ten lon/lat rows with many numeric and long text columns, for summary size limits."""
    df = points_lonlat().head(10).drop(columns=["site", "value"])
    for k in range(WIDE_NUMERIC_COLUMNS):
        df[f"measurement_{k:02d}"] = np.linspace(0, 1, len(df)) * (k + 1) / 3
    long_text = "with a long free-text description " * 3
    for k in range(WIDE_TEXT_COLUMNS):
        df[f"notes_{k}"] = [f"observation {r} {long_text}" for r in range(len(df))]
    df.to_csv(path, index=False)
    return path
