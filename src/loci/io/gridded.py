"""Gridded data such as reanalyses and climate model output: NetCDF files and Zarr stores (D-051).

They are read lazily into the same raster form as GeoTIFFs and STAC loads: an xarray Dataset with
one variable per band and dims (y, x) or (time, y, x), values as stored, with any scale, offset
and fill value kept as attributes. CF conventions say which dimensions are longitude, latitude
and time; what the data do not say is flagged rather than guessed:

- The CRS comes from the variables' grid_mapping. Without one, a longitude/latitude grid is taken
  to be WGS 84 (EPSG:4326), with a warning; any other grid has no CRS until crs= gives one.
- The variables read (bands=) must share their dimensions. Other dimensions than x, y and time,
  such as pressure level or ensemble member, are chosen with select=, unless they hold one value.
- A long time axis must be narrowed with select={"time": [start, end]}, and bbox= cuts out an
  area of a longitude/latitude grid, so an archive such as ERA5 is read only where it is needed.
- Longitudes from 0 to 360 are kept as they are, with a note; the map shows them shifted.
"""

import re
from pathlib import PurePath
from typing import Any

import numpy as np
import pandas as pd
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from pyproj import CRS
from pyproj.exceptions import CRSError

from loci.errors import LoadError
from loci.io.common import ReadResult, resolve_crs
from loci.io.formats import is_remote
from loci.models import CrsSource, ReadOptions

GRIDDED = ("netcdf", "zarr")
MAX_TIME_STEPS = 10_000  # read at once; a longer axis needs a time range
MAX_VARIABLES = 50
LISTED = 8  # choices shown when one is needed
X_NAMES = {"lon", "longitude", "x", "rlon", "nav_lon"}
Y_NAMES = {"lat", "latitude", "y", "rlat", "nav_lat"}
T_NAMES = {"time", "valid_time", "t"}
X_STANDARD = {"longitude", "projection_x_coordinate", "grid_longitude"}
Y_STANDARD = {"latitude", "projection_y_coordinate", "grid_latitude"}


def open_gridded(uri: str, fmt_name: str, options: ReadOptions) -> ReadResult:
    """A NetCDF file or Zarr store as a raster Dataset, read lazily; see the module docstring."""
    what = repr(PurePath(uri.rstrip("/")).name)
    full = _open(uri, fmt_name)
    variables = _variables(full, options.bands, what)
    declared = _declared_crs(full, variables, what)
    ds = full[variables]
    notes: list[str] = []
    ds, roles = _name_axes(ds, variables[0], what, notes)
    x_name = next(dim for dim, role in roles.items() if role == "x")
    ds = _select(ds, options.select or {}, what, notes)
    _check_time(ds, what)
    crs, crs_source, warnings = _crs(ds, x_name, declared, options.crs, what)
    ds = _cut(ds, options.bbox, crs, notes)
    if crs is not None:
        ds = ds.rio.write_crs(crs)
        if crs.is_geographic and float(ds["x"].max()) > 180:
            notes.append(
                "Longitudes run from 0 to 360 and are kept so; the map shows them from -180 to 180."
            )
    order = ["time", "y", "x"] if "time" in ds.dims else ["y", "x"]
    ds = _chunked(_storage_chunks(ds.transpose(*order), roles))
    return ReadResult(ds, "raster", fmt_name, options, crs_source, warnings, notes)


def _open(uri: str, fmt_name: str) -> xr.Dataset:
    # Values stay as stored (scale, offset and fill value as attributes), as for other rasters.
    # No dask yet: chunking a whole archive (ERA5 has 273 variables of 1.3 million hourly chunks
    # each) would take gigabytes before anything is read; the part chosen is chunked at the end.
    common: dict[str, Any] = {"mask_and_scale": False, "decode_timedelta": False, "chunks": None}
    try:
        if fmt_name == "netcdf":
            if is_remote(uri):
                raise LoadError(
                    "loci reads NetCDF files from disk.",
                    hint="Download the file first; data in the cloud are best read as Zarr.",
                )
            return xr.open_dataset(uri, engine="netcdf4", **common)
        # Remote stores honour the proxy settings of the computer, as a browser does.
        storage = {"client_kwargs": {"trust_env": True}} if is_remote(uri) else None
        try:  # consolidated metadata is one read; a store without it is read item by item
            return xr.open_zarr(uri, storage_options=storage, consolidated=True, **common)
        except (KeyError, FileNotFoundError):
            return xr.open_zarr(uri, storage_options=storage, consolidated=False, **common)
    except LoadError:
        raise
    except (OSError, ValueError, KeyError) as e:
        raise LoadError(f"Cannot open {uri} as {fmt_name}: {e}") from e


def _variables(ds: xr.Dataset, bands: list[str] | None, what: str) -> list[str]:
    """The variables to read: those named, or every gridded one if they share dimensions."""
    gridded = [str(v) for v in ds.data_vars if ds[v].ndim >= 2]
    if bands:
        missing = [b for b in bands if b not in ds.data_vars]
        if missing:
            raise LoadError(
                f"{what} has no variable {', '.join(map(repr, missing))}.",
                hint=f"Its gridded variables: {_listed(gridded)}.",
            )
        chosen = list(bands)
    else:
        chosen = gridded
    if not chosen:
        raise LoadError(f"{what} holds no gridded variables.")
    if len({ds[v].dims for v in chosen}) > 1:
        shapes = "; ".join(f"{v} {ds[v].dims}" for v in chosen[:LISTED])
        raise LoadError(
            f"The variables of {what} have different dimensions, so they cannot be one raster: "
            f"{shapes}{'; …' if len(chosen) > LISTED else ''}.",
            hint="Choose variables that share their dimensions with bands=[...].",
        )
    if len(chosen) > MAX_VARIABLES:
        raise LoadError(
            f"{what} holds {len(chosen)} variables.",
            hint=f"Choose up to {MAX_VARIABLES} with bands=[...], from {_listed(chosen)}.",
        )
    return chosen


def _name_axes(
    ds: xr.Dataset, first: str, what: str, notes: list[str]
) -> tuple[xr.Dataset, dict[str, str]]:
    """Rename the dimensions CF marks as longitude, latitude and time to x, y and time; also
    return each one's role, by its own name."""
    roles: dict[str, str] = {}
    for dim in ds[first].dims:
        role = _role(ds, str(dim))
        if role is not None and role not in roles.values():
            roles[str(dim)] = role
    found = set(roles.values())
    if not {"x", "y"} <= found:
        raise LoadError(
            f"Cannot tell which dimensions of {what} are x and y: {ds[first].dims}.",
            hint="loci needs CF attributes (axis, standard_name or units) or names such as lon "
            "and lat.",
        )
    renames = {dim: role for dim, role in roles.items() if dim != role}
    if not renames:
        return ds, roles
    clashing = [r for r in renames.values() if r in ds.variables and r not in ds.dims]
    ds = ds.drop_vars(clashing).rename(renames)
    notes.append("Read " + ", ".join(f"{dim} as {role}" for dim, role in renames.items()) + ".")
    return ds, roles


def _role(ds: xr.Dataset, dim: str) -> str | None:
    """x, y or time for a dimension, from its CF attributes, units or name."""
    coord = ds[dim] if dim in ds.coords else None
    attrs = coord.attrs if coord is not None else {}
    axis = str(attrs.get("axis", "")).upper()
    standard = str(attrs.get("standard_name", "")).lower()
    units = str(attrs.get("units", "")).lower()
    name = dim.lower()
    if axis == "X" or standard in X_STANDARD or re.fullmatch(r"degrees?_?e(ast)?", units):
        return "x"
    if axis == "Y" or standard in Y_STANDARD or re.fullmatch(r"degrees?_?n(orth)?", units):
        return "y"
    if axis == "T" or standard == "time" or name in T_NAMES:
        return "time"
    if coord is not None and np.issubdtype(coord.dtype, np.datetime64):
        return "time"
    return "x" if name in X_NAMES else "y" if name in Y_NAMES else None


def _select(ds: xr.Dataset, select: dict[str, Any], what: str, notes: list[str]) -> xr.Dataset:
    """Pick along dimensions: one value, or [start, end] for a range; one-valued ones go."""
    for dim, value in select.items():
        if dim not in ds.dims:
            raise LoadError(
                f"select= names {dim!r}, which is not a dimension of {what}.",
                hint=f"Its dimensions: {', '.join(map(str, ds.dims))}.",
            )
        ds = _pick(ds, dim, value, what)
        notes.append(f"Selected {dim} {_shown(value)}.")
    for extra in [str(d) for d in ds.dims if d not in ("x", "y", "time")]:
        values = ds[extra].values if extra in ds.coords else np.arange(ds.sizes[extra])
        if ds.sizes[extra] == 1:
            ds = ds.isel({extra: 0})
            notes.append(f"Took the only {extra}, {values[0]}.")
            continue
        raise LoadError(
            f"{what} has the dimension {extra} with {ds.sizes[extra]} values: {_listed(values)}.",
            hint=f"Choose one with select={{{extra!r}: value}}.",
        )
    return ds


def _pick(ds: xr.Dataset, dim: str, value: Any, what: str) -> xr.Dataset:
    coord = ds[dim] if dim in ds.coords else None
    if isinstance(value, list):
        if len(value) != 2:
            raise LoadError(f"select={{{dim!r}: [start, end]}} takes two values, not {value!r}.")
        start, end = (_typed(coord, v) for v in value)
        ascending = coord is None or coord.size < 2 or bool(coord[1] >= coord[0])
        picked = ds.sel({dim: slice(start, end) if ascending else slice(end, start)})
        if picked.sizes[dim] == 0:
            raise LoadError(
                f"{what} has no {dim} from {start} to {end}.",
                hint=f"Its {dim} runs from {_ends(coord)}.",
            )
        return picked
    try:
        return ds.sel({dim: _typed(coord, value)})
    except (KeyError, ValueError, TypeError) as e:
        values: Any = coord.values if coord is not None else []
        raise LoadError(
            f"{what} has no {dim} {value!r}.", hint=f"Its values: {_listed(values)}."
        ) from e


def _typed(coord: xr.DataArray | None, value: Any) -> Any:
    """A value as the coordinate holds them: "850" for a numeric level is 850."""
    if coord is not None and np.issubdtype(coord.dtype, np.number) and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _check_time(ds: xr.Dataset, what: str) -> None:
    if "time" not in ds.dims:
        return
    times = ds["time"]
    if not np.issubdtype(times.dtype, np.datetime64):
        calendar = times.encoding.get("calendar") or "an unusual"
        raise LoadError(
            f"{what} uses the {calendar} calendar, which loci cannot yet place on dates.",
            hint="Convert it to the standard calendar first, e.g. with xarray's convert_calendar.",
        )
    count = ds.sizes["time"]
    if count > MAX_TIME_STEPS:
        raise LoadError(
            f"{what} holds {count:,} time steps, from {_ends(times)}.",
            hint=f'Choose a period with select={{"time": ["2020-01-01", "2020-12-31"]}}; '
            f"at most {MAX_TIME_STEPS:,} steps are read at once.",
        )


def _declared_crs(ds: xr.Dataset, variables: list[str], what: str) -> CRS | None:
    """The CRS the variables' CF grid_mapping describes, if they name one."""
    for name in variables:
        mapping = ds[name].attrs.get("grid_mapping") or ds[name].encoding.get("grid_mapping")
        if mapping and str(mapping) in ds.variables:
            try:
                return CRS.from_cf(dict(ds[str(mapping)].attrs))
            except CRSError as e:
                raise LoadError(
                    f"The grid_mapping {mapping!r} of {what} does not describe a CRS: {e}"
                ) from e
    return None


def _crs(
    ds: xr.Dataset, x_name: str, declared: CRS | None, requested: str | None, what: str
) -> tuple[CRS | None, CrsSource | None, list[str]]:
    """The declared CRS, or the one given; for a longitude/latitude grid without either, WGS 84
    with a warning, as for a table of longitudes and latitudes."""
    if declared is None and requested is None and _lonlat(ds, x_name):
        return (
            CRS.from_epsg(4326),
            "assumed",
            [
                f"{what} names no grid_mapping, so its longitude/latitude grid is taken to be "
                "WGS 84 (EPSG:4326). Pass crs= if it is on another datum or sphere."
            ],
        )
    return resolve_crs(declared, requested, what=f"Grid {what}")


def _lonlat(ds: xr.Dataset, x_name: str) -> bool:
    """Whether x and y hold longitudes and latitudes, by their names or attributes and ranges."""
    x, y = ds["x"], ds["y"]
    attrs = {k: str(v).lower() for k, v in x.attrs.items()}
    named = (
        x_name.lower() in {"lon", "longitude"}
        or attrs.get("units", "").startswith("degree")
        or attrs.get("standard_name") == "longitude"
    )
    in_range = float(x.min()) >= -180 and float(x.max()) <= 360 and float(abs(y).max()) <= 90
    return named and in_range


def _cut(ds: xr.Dataset, bbox: Any, crs: CRS | None, notes: list[str]) -> xr.Dataset:
    """The area of `bbox` (west, south, east, north in degrees) of a longitude/latitude grid."""
    if bbox is None:
        return ds
    if crs is None or not crs.is_geographic:
        raise LoadError(
            "bbox= cuts out areas of longitude/latitude grids; this grid is not one.",
            hint='Choose ranges of x and y in the grid\'s own units: select={"x": [a, b], ...}.',
        )
    west, south, east, north = (float(v) for v in bbox)
    if float(ds["x"].max()) > 180 and west < 0:  # a 0-360 grid: name the area as it does
        west, east = west % 360, east % 360
        if west > east:
            raise LoadError(
                "This area crosses the 0° meridian of a grid that runs from 0 to 360.",
                hint="Load the two sides separately, or choose an area on one side.",
            )
    x_up = bool(ds["x"][-1] >= ds["x"][0])
    y_up = bool(ds["y"][-1] >= ds["y"][0])
    ds = ds.sel(
        x=slice(west, east) if x_up else slice(east, west),
        y=slice(south, north) if y_up else slice(north, south),
    )
    if ds.sizes["x"] == 0 or ds.sizes["y"] == 0:
        raise LoadError(f"bbox={list(bbox)} does not overlap the grid.")
    notes.append(f"Cut out bbox={list(bbox)}: {ds.sizes['y']} by {ds.sizes['x']} cells.")
    return ds


def _storage_chunks(ds: xr.Dataset, roles: dict[str, str]) -> xr.Dataset:
    """Keep each variable's chunks as stored, by the dimensions' new names: a read costs whole
    stored chunks, however little of them is wanted (see web.layers point values)."""
    for name in ds.data_vars:
        stored = ds[name].encoding.get("preferred_chunks") or {}
        renamed = {roles.get(dim, dim): size for dim, size in stored.items()}
        ds[name].encoding["preferred_chunks"] = {
            dim: size for dim, size in renamed.items() if dim in ds[name].dims
        }
    return ds


def _chunked(ds: xr.Dataset) -> xr.Dataset:
    """The chosen part, lazily with dask, in its stored chunks; data stored whole are chunked a
    time step at a time, so a computation over the period streams through it."""
    first = ds[next(iter(ds.data_vars))]
    stored = first.encoding.get("preferred_chunks") or {}
    chunks = {
        str(dim): min(stored.get(str(dim), ds.sizes[dim]), ds.sizes[dim]) for dim in first.dims
    }
    if not stored and "time" in chunks:
        chunks["time"] = 1
    encodings = {name: dict(ds[name].encoding) for name in ds.data_vars}
    ds = ds.chunk(chunks)
    for name, encoding in encodings.items():
        ds[name].encoding = encoding  # keep how the data are stored, for the cost of reads
    return ds


def _shown(value: Any) -> str:
    return f"from {value[0]} to {value[1]}" if isinstance(value, list) else repr(value)


def _ends(coord: xr.DataArray | None) -> str:
    if coord is None or coord.size == 0:
        return "unknown"
    first, last = coord.values[0], coord.values[-1]
    if np.issubdtype(coord.dtype, np.datetime64):
        return f"{pd.Timestamp(first).isoformat()} to {pd.Timestamp(last).isoformat()}"
    return f"{first} to {last}"


def _listed(values: Any) -> str:
    items = [str(v) for v in list(values)[:LISTED]]
    return ", ".join(items) + (", …" if len(values) > LISTED else "")
