"""Read tables of point coordinates (CSV, plain Parquet) as point GeoDataFrames."""

from pathlib import PurePath

import geopandas as gpd
import pandas as pd
from pyproj import CRS

from loci.errors import LoadError
from loci.io.common import ReadResult, crs_to_text, parse_crs
from loci.models import CrsSource, ReadOptions

X_NAMES = ("lon", "longitude", "long", "lng", "x", "easting")
Y_NAMES = ("lat", "latitude", "y", "northing")
LON_NAMES = frozenset({"lon", "longitude", "long", "lng"})
LAT_NAMES = frozenset({"lat", "latitude"})
WGS84 = CRS.from_epsg(4326)


def read_points_table(uri: str, fmt: str, options: ReadOptions) -> ReadResult:
    """Build point geometries from two coordinate columns. Every row is kept.

    Rows with a missing or non-numeric coordinate get a null geometry and are counted in a
    warning. The CRS follows `crs_for_table`.
    """
    df = _read_table(uri, fmt)
    x_col, y_col = _coordinate_columns(df, options)
    x = pd.to_numeric(df[x_col], errors="coerce")
    y = pd.to_numeric(df[y_col], errors="coerce")
    bad = (x.isna() | y.isna()).to_numpy()

    geometry = gpd.points_from_xy(x, y)
    geometry[bad] = None
    crs, crs_source, warnings = crs_for_table(x[~bad], y[~bad], x_col, y_col, options.crs)
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=crs)

    if bad.any():
        warnings.append(
            f"{int(bad.sum())} of {len(df)} rows have a missing or non-numeric coordinate in "
            f"{x_col!r}/{y_col!r}; they are kept with a null geometry."
        )
    resolved = ReadOptions(
        x_col=x_col, y_col=y_col, crs=crs_to_text(crs) if crs is not None else None
    )
    return ReadResult(gdf, "vector", fmt, resolved, crs_source, warnings)


def crs_for_table(
    x: pd.Series, y: pd.Series, x_col: str, y_col: str, requested: str | None
) -> tuple[CRS | None, CrsSource | None, list[str]]:
    """The CRS of a coordinate table.

    A requested CRS is used as given. Otherwise EPSG:4326 is assumed only when the columns are
    named like longitude/latitude and every value is in range; the assumption is flagged. In all
    other cases the CRS stays undefined, with a warning.
    """
    if requested is not None:
        return parse_crs(requested), "argument", []

    named_lonlat = x_col.lower() in LON_NAMES and y_col.lower() in LAT_NAMES
    in_range = len(x) > 0 and bool(x.between(-180, 180).all() and y.between(-90, 90).all())
    if named_lonlat and in_range:
        return (
            WGS84,
            "assumed",
            [
                f"No CRS given; assumed EPSG:4326 (WGS 84 longitude/latitude) because columns "
                f"{x_col!r}/{y_col!r} are named and ranged like longitude/latitude. Pass crs= "
                "if the coordinates use another datum."
            ],
        )
    reason = (
        "their values are outside longitude/latitude ranges"
        if named_lonlat
        else "they are not named like longitude/latitude"
    )
    return (
        None,
        None,
        [
            f"No CRS given for columns {x_col!r}/{y_col!r}, and {reason}, so the CRS is "
            "undefined. Pass crs= (for example the EPSG code of the projection)."
        ],
    )


def detect_coordinate_columns(columns: list[str]) -> tuple[str, str]:
    """Find the x and y columns by name, case-insensitively. Ambiguity is an error."""
    by_lower = {c.lower(): c for c in columns}
    xs = [by_lower[n] for n in X_NAMES if n in by_lower]
    ys = [by_lower[n] for n in Y_NAMES if n in by_lower]
    if len(xs) == 1 and len(ys) == 1:
        return xs[0], ys[0]
    if xs and ys:
        raise LoadError(
            f"Ambiguous coordinate columns: x could be {xs}, y could be {ys}.",
            hint="Pass x_col= and y_col=.",
        )
    raise LoadError(
        f"No coordinate columns found among {_preview(columns)}.",
        hint=f"Pass x_col= and y_col=. Recognised names: x {list(X_NAMES)}, y {list(Y_NAMES)}.",
    )


def _coordinate_columns(df: pd.DataFrame, options: ReadOptions) -> tuple[str, str]:
    if options.x_col is None and options.y_col is None:
        return detect_coordinate_columns([str(c) for c in df.columns])
    if options.x_col is None or options.y_col is None:
        raise LoadError("Pass both x_col= and y_col=, or neither to detect them.")
    missing = [c for c in (options.x_col, options.y_col) if c not in df.columns]
    if missing:
        raise LoadError(
            f"Column(s) {missing} not found; available: {_preview(list(df.columns))}.",
        )
    return options.x_col, options.y_col


def _read_table(uri: str, fmt: str) -> pd.DataFrame:
    try:
        return pd.read_csv(uri) if fmt == "csv" else pd.read_parquet(uri)
    except (OSError, ValueError) as e:
        raise LoadError(f"Cannot read {PurePath(uri).name!r} as a table: {e}") from e


def _preview(columns: list[str], limit: int = 10) -> str:
    shown = ", ".join(repr(c) for c in columns[:limit])
    more = len(columns) - limit
    return f"[{shown}{f', ... ({more} more)' if more > 0 else ''}]"
