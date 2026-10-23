"""The `stac_search` tool: find the items a STAC catalogue holds for a place and a period."""

from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

import geopandas as gpd
import pandas as pd
import pystac
import shapely
import xarray as xr
from pyproj import Transformer
from shapely.geometry import mapping

from loci import provenance
from loci.errors import LociError
from loci.io import STAC_ITEMS, read
from loci.io.stac import EARTH_SEARCH, loadable_bands, save_items, search_items
from loci.models import (
    Bounds,
    ItemRow,
    MonthCount,
    NumericStats,
    ReadOptions,
    SourceInput,
    StacSearchResult,
)
from loci.summary.common import iso, sig
from loci.workspace import Workspace

DEFAULT_COLLECTION = "sentinel-2-l2a"
DEFAULT_LIMIT = 500
MAX_MONTHS = 24
SAMPLE_ROWS = 5
MAX_LISTED = 30
MAX_AOI_VERTICES = 1000


def stac_search(
    ws: Workspace,
    *,
    start: str,
    end: str,
    bbox: list[float] | None = None,
    aoi: str | None = None,
    collection: str = DEFAULT_COLLECTION,
    max_cloud: float | None = None,
    catalog: str = EARTH_SEARCH,
    limit: int = DEFAULT_LIMIT,
) -> StacSearchResult:
    """Search a STAC catalogue; return a data_ref for the items and a compact account of them.

    Args:
        start, end: First and last day, as ISO dates (YYYY-MM-DD), both included.
        bbox: Area as [west, south, east, north] in degrees. Give this or aoi.
        aoi: data_ref of a layer whose footprint is the area. Give this or bbox.
        collection: Collection id, e.g. "sentinel-2-l2a" or "landsat-c2-l2".
        max_cloud: Keep items with cloud cover below this percentage.
        catalog: STAC API URL; Earth Search by default.
        limit: Most items to fetch.

    The data_ref is a vector layer of item footprints. Load pixels with
    load(data_ref, bands=[...]).
    """
    area, intersects, area_notes = _area(ws, bbox, aoi)
    first_day, last_day = _dates(start, end)
    if max_cloud is not None and not 0 <= max_cloud <= 100:
        raise LociError("max_cloud is a percentage between 0 and 100.")
    if limit < 1:
        raise LociError("limit must be at least 1.")

    items, matched = search_items(
        catalog=catalog,
        collection=collection,
        bbox=None if intersects else area,
        intersects=intersects,
        datetime_range=f"{first_day}/{last_day}",
        max_cloud=max_cloud,
        limit=limit,
    )
    search = {
        "catalog": catalog,
        "collection": collection,
        "bbox": list(area),
        "aoi": aoi,
        "start": first_day,
        "end": last_day,
        "max_cloud": max_cloud,
        "limit": limit,
        "matched": matched,
        "searched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    data_ref = ws.new_ref("vector")
    path = ws.stac_dir / f"{data_ref}.json"
    save_items(items, path, search)
    frame = read(str(path), STAC_ITEMS, ReadOptions())
    ws.register(frame, str(path), data_ref=data_ref)
    arguments = {
        "start": start,
        "end": end,
        "bbox": bbox,
        "aoi": aoi,
        "collection": collection,
        "max_cloud": max_cloud,
        "catalog": catalog,
        "limit": limit,
    }
    inputs = [SourceInput(uri=catalog, stac_items=[item.id for item in items])]
    if aoi is not None:
        inputs.append(SourceInput(name="aoi", data_ref=aoi))
    provenance.write(
        ws,
        provenance.new_record(
            produced_by="stac_search",
            data_ref=data_ref,
            call=provenance.tool_call("stac_search", arguments),
            inputs=inputs,
            parameters=search,
        ),
    )
    return _summarise(data_ref, search, frame.data, items, area_notes)


def _area(
    ws: Workspace, bbox: list[float] | None, aoi: str | None
) -> tuple[Bounds, dict[str, Any] | None, list[str]]:
    """The search area as a bbox, plus a GeoJSON geometry when an AOI layer gives one."""
    if (bbox is None) == (aoi is None):
        raise LociError("Give the search area as either bbox= or aoi=, not both or neither.")
    if bbox is not None:
        if len(bbox) != 4:
            raise LociError("bbox is [west, south, east, north] in degrees.")
        west, south, east, north = (float(v) for v in bbox)
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise LociError(
                f"bbox {bbox} is not [west, south, east, north] in degrees.",
                hint="An area crossing the antimeridian has to be searched as two boxes.",
            )
        return (west, south, east, north), None, []

    assert aoi is not None
    data = ws.open(aoi)
    if isinstance(data, xr.Dataset):
        if data.rio.crs is None:
            raise LociError(f"{aoi} has no CRS, so its area cannot be located.")
        to_wgs84 = Transformer.from_crs(data.rio.crs, 4326, always_xy=True)
        left, bottom, right, top = data.rio.bounds()
        west, south, east, north = to_wgs84.transform_bounds(
            left, bottom, right, top, densify_pts=21
        )
        return (west, south, east, north), None, []
    gdf: gpd.GeoDataFrame = data
    if gdf.crs is None:
        raise LociError(f"{aoi} has no CRS, so its area cannot be located.")
    shape = gdf.to_crs(4326).geometry.union_all()
    notes = []
    if shapely.get_num_coordinates(shape) > MAX_AOI_VERTICES:
        shape = shape.simplify(0.0001, preserve_topology=True)  # about 10 m
        notes.append("The AOI outline was simplified to about 10 m for the search request.")
    west, south, east, north = (float(v) for v in shape.bounds)
    return (west, south, east, north), mapping(shape), notes


def _dates(start: str, end: str) -> tuple[str, str]:
    try:
        first, last = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError as e:
        raise LociError(f"Dates must be ISO dates (YYYY-MM-DD): {e}") from e
    if first > last:
        raise LociError(f"start {first} is after end {last}.")
    return first.isoformat(), last.isoformat()


def _summarise(
    data_ref: str,
    search: dict[str, Any],
    frame: gpd.GeoDataFrame,
    items: list[pystac.Item],
    area_notes: list[str],
) -> StacSearchResult:
    times = frame["datetime"]
    days = times.dt.normalize()
    cloud = frame["cloud_cover"].dropna()
    months = []
    for month, group in frame.groupby(times.dt.to_period("M")):
        clouds = group["cloud_cover"].dropna()
        months.append(
            MonthCount(
                month=str(month),
                items=len(group),
                days=int(group["datetime"].dt.normalize().nunique()),
                median_cloud_cover=sig(clouds.median()) if len(clouds) else None,
            )
        )
    crs = Counter(f"EPSG:{e}" for e in frame["epsg"].dropna())
    grids = Counter(str(g) for g in frame["grid"].dropna())
    sample = frame.sort_values("datetime").head(SAMPLE_ROWS)

    warnings, notes = [], list(area_notes)
    matched = search["matched"]
    if not items:
        warnings.append("No items matched. Widen the dates, raise max_cloud or check the area.")
    elif matched is not None and matched > len(items):
        warnings.append(
            f"Only the first {len(items)} of {matched} matching items were fetched "
            f"(limit={search['limit']}). Narrow the search or raise limit."
        )
    if len(crs) > 1:
        notes.append(f"The items span {len(crs)} CRSs; loading them together needs output_crs=.")
    if items:
        notes.append(
            f"Load pixels with load('{data_ref}', bands=[...]). The data_ref is also a vector "
            "layer of footprints for inspect, validate (time_column='datetime') and preview."
        )
    return StacSearchResult(
        data_ref=data_ref,
        catalog=search["catalog"],
        collection=search["collection"],
        bbox=tuple(search["bbox"]),
        start=search["start"],
        end=search["end"],
        max_cloud=search["max_cloud"],
        items=len(items),
        matched=matched,
        first=iso(days.min()) if len(frame) else None,
        last=iso(days.max()) if len(frame) else None,
        days=int(days.nunique()),
        cloud_cover=(
            NumericStats(min=sig(cloud.min()), max=sig(cloud.max()), mean=sig(cloud.mean()))
            if len(cloud)
            else None
        ),
        crs=dict(crs.most_common(MAX_LISTED)),
        grids=dict(grids.most_common(10)),
        by_month=months[:MAX_MONTHS],
        months_omitted=max(len(months) - MAX_MONTHS, 0),
        sample=[
            ItemRow(
                id=row.id,
                datetime=iso(row.datetime),
                cloud_cover=sig(row.cloud_cover) if pd.notna(row.cloud_cover) else None,
                grid=row.grid if isinstance(row.grid, str) else None,
            )
            for row in sample.itertuples()
        ],
        bands=loadable_bands(items[0])[:MAX_LISTED] if items else [],
        warnings=warnings,
        notes=notes,
    )
