"""Synthetic datasets with exactly known answers.

Used by the test suite and, from M6, by the benchmark. Each writer is deterministic, writes one
small file and returns its path. The constants beside each writer are the answers tests check.
All projected fixtures use WGS 84 / UTM zone 33N with their upper-left corner at ORIGIN.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from pyproj import CRS, Geod, Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box, mapping

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

# Cloudy stack: as the time stack, but in May and June only the top 2 of 10 rows are valid.
CLOUDY_MONTHS = (5, 6)
CLOUDY_VALID_FRACTION = 0.2

# Gappy stack: April and August are missing, leaving two gaps of 61 and 62 days.
GAPPY_MISSING_MONTHS = (4, 8)
GAPPY_GAP_DAYS = (61.0, 62.0)

# NDVI raster: values run from -0.2 to 0.8 across columns; the bad variant has a block of 1.2.
NDVI_SIZE = 50
NDVI_MIN, NDVI_MAX, NDVI_BAD = -0.2, 0.8, 1.2

# Grid variants of the ramp: 20 m pixels, and 10 m pixels shifted half a pixel east.
COARSE_RES = 20.0
SHIFT_X = 5.0

# STAC scenes: three dates of a 40 x 40 pixel, 10 m scene with red and nir bands (uint16,
# scale 0.0001, offset -0.1, nodata 0) and a 20 m "scl" band. Red on scene k is 1000 + 100 k.
STAC_DATES = ("2024-03-04", "2024-03-14", "2024-03-24")
STAC_CLOUD = (30.0, 5.0, 60.0)
STAC_CLEAREST = 1
STAC_SIZE = 40
STAC_RES = 10.0
STAC_RED = (1000, 1100, 1200)
STAC_NIR = 3000
STAC_SCALE, STAC_OFFSET = 0.0001, -0.1
# Cloud on a STAC scene covers its top rows: red and nir are bright there (CLOUD_DN), and the 20 m
# scene classification says cloud (SCL_CLOUD). Elsewhere it says vegetation (SCL_CLEAR).
SCL_CLEAR, SCL_CLOUD = 4, 9
CLOUD_DN = 4000
STAC_EXTENSIONS = [
    "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
    "https://stac-extensions.github.io/raster/v1.1.0/schema.json",
    "https://stac-extensions.github.io/eo/v1.1.0/schema.json",
]

# Clean polygons: a 2 x 2 grid of 100 m squares.
POLY_SIZE = 100.0
POLY_AREA = POLY_SIZE**2
POLY_COUNT = 4
POLY_LANDUSE = ["forest", "urban", "forest", "water"]
POLY_SCORE = [0.5, 1.5, 2.5, 3.5]
POLY_SCORE_MEAN = 2.0
POLY_SURVEYED = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]

# Catchments: the grid's west and east columns, 200 m tall and 100 m wide (20,000 m2 each).
# Overlaying the land-use grid on them gives each square a catchment.
CATCHMENTS = ("west", "east")
CATCHMENT_AREA = 2 * POLY_AREA
POLY_CATCHMENT = ["west", "east", "west", "east"]

# Dirty polygons: valid, self-intersecting bowtie, empty, null (in that row order).
DIRTY_INVALID_ROW = 1

# Catchment DEM: 20 x 20 pixels of 10 m under the two catchments; elevation rises 5 m per column
# from 100 m. The top two rows of the east catchment are nodata, which leaves both means intact.
DEM_SIZE = 20
DEM_BASE, DEM_STEP = 100.0, 5.0
DEM_NODATA = -9999.0
DEM_NODATA_ROWS = 2
DEM_WEST_MEAN, DEM_EAST_MEAN = 122.5, 172.5

# Sample sites over the NDVI raster: eight at pixel centres of the columns below (mean NDVI exactly
# 0.3) and two east of the raster. Easting/northing in UTM 33N; the CSV does not state the CRS.
SAMPLE_COLUMNS = (0, 7, 14, 21, 28, 35, 42, 49)
SAMPLE_OUTSIDE = 2
SAMPLE_NDVI_MEAN = 0.3

# Terrain in degrees: 100 x 100 pixels of 0.0001 degrees (about 9 m east-west and 11 m north-south)
# from 15 E, 37.01 N. Heights rise eastwards at 10 degrees across the west 40 columns and at 20
# degrees across the east 60, so 60% of the area is steeper than 15 degrees.
SLOPE_SIZE = 100
SLOPE_RES_DEG = 0.0001
SLOPE_ORIGIN = (15.0, 37.01)
SLOPE_BREAK_COLUMN = 40
SLOPE_GENTLE_DEG, SLOPE_STEEP_DEG = 10.0, 20.0
SLOPE_BASE = 200.0
SLOPE_STEEP_PERCENT = 60.0

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
    origin: tuple[float, float] = ORIGIN,
) -> Path:
    count, rows, cols = bands.shape
    profile: dict[str, object] = {
        "driver": "GTiff",
        "width": cols,
        "height": rows,
        "count": count,
        "dtype": bands.dtype.name,
        "crs": crs,
        "transform": from_origin(*origin, res, res),
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
    """Monthly NDVI stack with dims (time, y, x); the 15th of month m holds the value 0.05 * m."""
    return _monthly_stack(range(1, TS_STEPS + 1))


def cloudy_time_stack() -> xr.Dataset:
    """The monthly stack with only the top 2 rows valid (the rest NaN) in CLOUDY_MONTHS."""
    return _monthly_stack(range(1, TS_STEPS + 1), cloudy=CLOUDY_MONTHS)


def gappy_time_stack() -> xr.Dataset:
    """The monthly stack without GAPPY_MISSING_MONTHS."""
    months = [m for m in range(1, TS_STEPS + 1) if m not in GAPPY_MISSING_MONTHS]
    return _monthly_stack(months)


def _monthly_stack(months: Iterable[int], cloudy: Iterable[int] = ()) -> xr.Dataset:
    months = list(months)
    times = pd.to_datetime([f"2024-{m:02d}-15" for m in months])
    x = ORIGIN[0] + 10.0 * (np.arange(TS_SIZE) + 0.5)
    y = ORIGIN[1] - 10.0 * (np.arange(TS_SIZE) + 0.5)
    values = (0.05 * np.array(months, dtype="float32"))[:, None, None] * np.ones(
        (TS_SIZE, TS_SIZE), dtype="float32"
    )
    valid_rows = round(CLOUDY_VALID_FRACTION * TS_SIZE)
    for i, month in enumerate(months):
        if month in set(cloudy):
            values[i, valid_rows:] = np.nan
    ndvi = xr.DataArray(values, coords={"time": times, "y": y, "x": x}, dims=("time", "y", "x"))
    return xr.Dataset({"ndvi": ndvi.rio.write_nodata(np.nan)}).rio.write_crs(UTM33N)


def write_ndvi_raster(path: Path, *, out_of_range: bool = False) -> Path:
    """float32 band described 'ndvi', NaN nodata; values from NDVI_MIN to NDVI_MAX across columns.

    With out_of_range, a 5 x 5 block in the centre holds NDVI_BAD.
    """
    values = np.tile(np.linspace(NDVI_MIN, NDVI_MAX, NDVI_SIZE, dtype="float32"), (NDVI_SIZE, 1))
    if out_of_range:
        centre = NDVI_SIZE // 2
        values[centre : centre + 5, centre : centre + 5] = NDVI_BAD
    return _write_geotiff(
        path, values[None], crs=UTM33N, res=10.0, nodata=float("nan"), descriptions=["ndvi"]
    )


def write_coarse_raster(path: Path) -> Path:
    """The ramp's footprint at COARSE_RES (20 m) pixels."""
    rows, cols = round(RAMP_ROWS * RAMP_RES / COARSE_RES), round(RAMP_COLS * RAMP_RES / COARSE_RES)
    ones = np.ones((1, rows, cols), dtype="float32")
    return _write_geotiff(path, ones, crs=UTM33N, res=COARSE_RES, nodata=RAMP_NODATA)


def write_shifted_raster(path: Path) -> Path:
    """Like the ramp grid (10 m pixels) but with its origin SHIFT_X metres east: half a pixel."""
    ones = np.ones((1, RAMP_ROWS, RAMP_COLS), dtype="float32")
    origin = (ORIGIN[0] + SHIFT_X, ORIGIN[1])
    return _write_geotiff(path, ones, crs=UTM33N, res=RAMP_RES, nodata=RAMP_NODATA, origin=origin)


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


def catchments() -> gpd.GeoDataFrame:
    """Two catchments covering the polygon grid: 'west' (left column) and 'east' (right)."""
    x0, y0 = ORIGIN
    top, bottom = y0, y0 - 2 * POLY_SIZE
    shapes = [box(x0 + c * POLY_SIZE, bottom, x0 + (c + 1) * POLY_SIZE, top) for c in range(2)]
    return gpd.GeoDataFrame({"catchment": list(CATCHMENTS)}, geometry=shapes, crs=UTM33N)


def landuse_by_catchment() -> gpd.GeoDataFrame:
    """The polygon grid with the catchment each square falls in: an exact land-use overlay."""
    return polygons().assign(catchment=POLY_CATCHMENT)


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


def write_catchments(path: Path) -> Path:
    """Write the catchments; as with write_polygons, GeoJSON is written in WGS 84."""
    gdf = catchments()
    (gdf.to_crs("EPSG:4326") if path.suffix.lower() == ".geojson" else gdf).to_file(path)
    return path


def write_catchment_dem(path: Path) -> Path:
    """float32 elevation under the catchments: DEM_BASE + DEM_STEP * column, some nodata."""
    values = np.tile(DEM_BASE + DEM_STEP * np.arange(DEM_SIZE), (DEM_SIZE, 1)).astype("float32")
    values[:DEM_NODATA_ROWS, DEM_SIZE // 2 :] = DEM_NODATA
    return _write_geotiff(
        path, values[None], crs=UTM33N, res=10.0, nodata=DEM_NODATA, descriptions=["elevation"]
    )


def write_two_slope_dem(path: Path) -> Path:
    """float32 heights in metres on a grid in degrees (EPSG:4326): two planes rising eastwards."""
    lon0, lat0 = SLOPE_ORIGIN
    latitudes = lat0 - (np.arange(SLOPE_SIZE) + 0.5) * SLOPE_RES_DEG
    geod = Geod(ellps="WGS84")
    # Metres per degree of longitude along each row, from a short geodesic.
    per_degree = np.array([geod.line_length([lon0, lon0 + 0.001], [lat, lat]) for lat in latitudes])
    per_degree *= 1000
    east = np.outer(per_degree, (np.arange(SLOPE_SIZE) + 0.5) * SLOPE_RES_DEG)  # metres from edge
    break_at = per_degree[:, None] * SLOPE_BREAK_COLUMN * SLOPE_RES_DEG
    gentle, steep = np.tan(np.radians([SLOPE_GENTLE_DEG, SLOPE_STEEP_DEG]))
    heights = SLOPE_BASE + np.where(
        east < break_at, gentle * east, gentle * break_at + steep * (east - break_at)
    )
    return _write_geotiff(
        path,
        heights.astype("float32")[None],
        crs="EPSG:4326",
        res=SLOPE_RES_DEG,
        nodata=None,
        descriptions=["elevation"],
        origin=SLOPE_ORIGIN,
    )


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


def write_sample_sites_csv(path: Path) -> Path:
    """Ten sites with easting/northing in UTM 33N: eight on NDVI pixel centres, two east of it."""
    x0, y0 = ORIGIN
    inside = [(x0 + (c + 0.5) * 10, y0 - (5 * k + 3.5) * 10) for k, c in enumerate(SAMPLE_COLUMNS)]
    beyond = x0 + NDVI_SIZE * 10.0
    outside = [(beyond + 55.0, y0 - 105.0), (beyond + 155.0, y0 - 255.0)][:SAMPLE_OUTSIDE]
    sites = inside + outside
    pd.DataFrame(
        {
            "site": [f"P{k:02d}" for k in range(1, len(sites) + 1)],
            "easting": [x for x, _ in sites],
            "northing": [y for _, y in sites],
        }
    ).to_csv(path, index=False)
    return path


def write_points_blank_csv(path: Path) -> Path:
    """Five lon/lat rows, two of which have a missing or non-numeric coordinate."""
    path.write_text(
        "site,lon,lat,value\n"
        "A,15.00,37.00,1\n"
        "B,,37.01,2\n"
        "C,15.02,n/a,3\n"
        "D,15.03,37.03,4\n"
        "E,15.04,37.04,5\n",
        encoding="utf-8",
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


def stac_items(
    directory: Path,
    *,
    offsets: Iterable[float] | None = None,
    properties: dict[str, object] | None = None,
    red: Iterable[int] = STAC_RED,
    cloud_rows: Iterable[int] | None = None,
) -> list[pystac.Item]:
    """Three synthetic Sentinel-2-like scenes as STAC items whose assets are local GeoTIFFs.

    Items carry the projection, raster and eo extension fields odc-stac reads. `offsets` overrides
    each item's offset, e.g. to imitate a change of processing baseline between scenes;
    `properties` are added to every item. `red` sets each scene's red value, and `cloud_rows`
    covers the top rows of each scene with cloud (an even number, so whole 20 m SCL pixels).
    """
    directory.mkdir(parents=True, exist_ok=True)
    offsets = list(offsets) if offsets is not None else [STAC_OFFSET] * len(STAC_DATES)
    cloud_rows = list(cloud_rows) if cloud_rows is not None else [0] * len(STAC_DATES)
    if any(rows % 2 for rows in cloud_rows):
        raise ValueError(f"cloud_rows must be even, to cover whole 20 m SCL pixels: {cloud_rows}")
    x0, y0 = ORIGIN
    extent = STAC_SIZE * STAC_RES
    to_lonlat = Transformer.from_crs(UTM33N, "EPSG:4326", always_xy=True)
    west, south, east, north = to_lonlat.transform_bounds(x0, y0 - extent, x0 + extent, y0)
    items = []
    scenes = zip(STAC_DATES, STAC_CLOUD, red, offsets, cloud_rows, strict=True)
    for date, cloud, red_value, offset, rows in scenes:
        red_band = np.full((STAC_SIZE, STAC_SIZE), red_value, dtype="uint16")
        nir_band = np.full((STAC_SIZE, STAC_SIZE), STAC_NIR, dtype="uint16")
        scl_band = np.full((STAC_SIZE // 2, STAC_SIZE // 2), SCL_CLEAR, dtype="uint8")
        red_band[:rows] = nir_band[:rows] = CLOUD_DN
        scl_band[: rows // 2] = SCL_CLOUD
        bands = {
            "red": (red_band, STAC_RES),
            "nir": (nir_band, STAC_RES),
            "scl": (scl_band, 2 * STAC_RES),
        }
        item = pystac.Item(
            id=f"SYN_{date.replace('-', '')}",
            geometry=mapping(box(west, south, east, north)),
            bbox=[west, south, east, north],
            datetime=datetime.fromisoformat(date).replace(hour=10, tzinfo=UTC),
            properties={
                "eo:cloud_cover": cloud,
                "proj:epsg": 32633,
                "platform": "synthetic",
                **(properties or {}),
            },
            stac_extensions=STAC_EXTENSIONS,
        )
        for name, (values, res) in bands.items():
            path = _write_geotiff(
                directory / f"{item.id}_{name}.tif",
                values[None],
                crs=UTM33N,
                res=res,
                nodata=0,
                block=16,
                overviews=(2, 4),  # like real COGs, so coarse reads use them
            )
            raster: dict[str, object] = {
                "nodata": 0,
                "data_type": values.dtype.name,
                "spatial_resolution": res,
            }
            if name != "scl":
                raster.update(scale=STAC_SCALE, offset=offset)
            item.add_asset(
                name,
                pystac.Asset(
                    href=str(path.resolve()),
                    media_type=pystac.MediaType.COG,
                    roles=["data"],
                    extra_fields={
                        "gsd": res,
                        "proj:shape": list(values.shape),
                        "proj:transform": [res, 0.0, x0, 0.0, -res, y0],
                        "raster:bands": [raster],
                    },
                ),
            )
        items.append(item)
    return items


# Gridded climate data (NetCDF, Zarr): monthly 2 m temperature over 2001-2020 on a 0.25 degree
# longitude/latitude grid around Sicily, as from the Copernicus Climate Data Store: no
# grid_mapping, latitude descending, time named valid_time, values in kelvin.
CLIMATE_START, CLIMATE_YEARS = 2001, 20
CLIMATE_TREND = 0.3  # kelvin per decade, which is also degrees Celsius per decade
CLIMATE_BASE = 288.15  # kelvin (15 degrees C) at 37 N in 2001
CLIMATE_SEASON = 8.0  # kelvin: the amplitude of the seasonal cycle, peaking in July
CLIMATE_LAPSE = 0.5  # kelvin cooler per degree of latitude north of 37 N
CLIMATE_LATS = np.round(np.arange(38.0, 35.99, -0.25), 2)  # 9 rows, north to south
CLIMATE_LONS = np.round(np.arange(14.0, 16.01, 0.25), 2)  # 9 columns
CLIMATE_LEVELS = [1000, 850, 500]  # hPa, for a variable that has them


def climate_dataset(*, levels: bool = False) -> xr.Dataset:
    """The monthly temperature grid. Each year's mean rises by exactly CLIMATE_TREND / 10 K:
    the seasonal cycle averages to zero over every whole year."""
    months = pd.date_range(f"{CLIMATE_START}-01-01", periods=CLIMATE_YEARS * 12, freq="MS")
    years = (months.year - CLIMATE_START) + (months.month - 0.5) / 12
    season = CLIMATE_SEASON * np.cos(2 * np.pi * (months.month - 7) / 12)
    seasonal = CLIMATE_BASE + CLIMATE_TREND / 10 * np.asarray(years) + np.asarray(season)
    north = CLIMATE_LAPSE * (CLIMATE_LATS - 37.0)
    by_latitude = seasonal[:, None, None] - north[None, :, None]  # time, latitude, 1
    values = np.repeat(by_latitude, len(CLIMATE_LONS), axis=2)
    dims = ["valid_time", "latitude", "longitude"]
    coords: dict[str, object] = {
        "valid_time": months,
        "latitude": (
            "latitude",
            CLIMATE_LATS,
            {"units": "degrees_north", "standard_name": "latitude"},
        ),
        "longitude": (
            "longitude",
            CLIMATE_LONS,
            {"units": "degrees_east", "standard_name": "longitude"},
        ),
    }
    if levels:  # colder aloft: 6.5 K per km, roughly 1.5 km per 150 hPa
        drop = np.array([0.0, 9.75, 35.0])
        values = values[:, None, :, :] - drop[None, :, None, None]
        dims = ["valid_time", "pressure_level", "latitude", "longitude"]
        coords["pressure_level"] = ("pressure_level", CLIMATE_LEVELS, {"units": "hPa"})
    t2m = xr.DataArray(
        values.astype("float32"),
        dims=dims,
        attrs={
            "units": "K",
            "long_name": "2 metre temperature",
            "standard_name": "air_temperature",
        },
    )
    return xr.Dataset({"t" if levels else "t2m": t2m}, coords=coords)


def write_climate_netcdf(path: Path, *, levels: bool = False) -> Path:
    climate_dataset(levels=levels).to_netcdf(path, engine="netcdf4")
    return path


def write_climate_zarr(path: Path) -> Path:
    # Zarr format 2 with consolidated metadata, as the public ERA5 archives are stored.
    climate_dataset().to_zarr(path, mode="w", zarr_format=2, consolidated=True)
    return path


def write_climate_0_360_netcdf(path: Path) -> Path:
    """The climate grid placed across the 0 meridian on longitudes that run from 0 to 360."""
    ds = climate_dataset()
    ds = ds.assign_coords(longitude=np.round(np.arange(355.0, 357.01, 0.25), 2))
    ds["longitude"].attrs = {"units": "degrees_east", "standard_name": "longitude"}
    ds.to_netcdf(path, engine="netcdf4")
    return path


def write_projected_netcdf(path: Path) -> Path:
    """A UTM 33N grid of the ramp's values with a CF grid_mapping, as a model might write it."""
    x0, y0 = ORIGIN
    xs = x0 + 5 + 10 * np.arange(RAMP_COLS)
    ys = y0 - 5 - 10 * np.arange(RAMP_ROWS)
    values = np.tile(np.arange(RAMP_COLS, dtype="float32"), (RAMP_ROWS, 1))
    crs = CRS.from_user_input(UTM33N)
    ds = xr.Dataset(
        {"value": (("y", "x"), values, {"grid_mapping": "crs", "units": "1"})},
        coords={
            "x": ("x", xs, {"standard_name": "projection_x_coordinate", "units": "m"}),
            "y": ("y", ys, {"standard_name": "projection_y_coordinate", "units": "m"}),
        },
    )
    ds["crs"] = xr.DataArray(0, attrs=crs.to_cf())
    ds.to_netcdf(path, engine="netcdf4")
    return path


def write_noleap_netcdf(path: Path) -> Path:
    """The climate grid on a 365-day calendar, as climate models often keep time."""
    ds = climate_dataset().isel(valid_time=slice(0, 12))
    days = np.arange(12) * 30.0
    ds = ds.assign_coords(valid_time=("valid_time", days))
    ds["valid_time"].attrs = {"units": f"days since {CLIMATE_START}-01-01", "calendar": "noleap"}
    ds.to_netcdf(path, engine="netcdf4")
    return path
