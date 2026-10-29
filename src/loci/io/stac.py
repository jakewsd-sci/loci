"""STAC: search a catalogue, keep the items it returns, and load their pixels as a time stack.

Search results are held as a GeoJSON FeatureCollection of the items, with the search parameters
under "loci:search", and read back as a GeoDataFrame of footprints (a vector layer). Loading
pixels goes through odc-stac into the usual raster Dataset, with dims (time, y, x).
"""

import json
import warnings
from collections import Counter
from collections.abc import Sequence
from itertools import islice
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import odc.stac
import pandas as pd
import pystac
import pystac_client
import pystac_client.exceptions
import rasterio
from pyproj import CRS
from shapely.geometry import shape

from loci.errors import LoadError, LociError
from loci.io.common import ReadResult, crs_to_text, parse_crs
from loci.models import Bounds, CrsSource, ReadOptions

# odc-loader reads overviews by warping from a dataset opened without its transform, and rasterio
# warns from its warp module although the values are right (tests check them). Silence exactly
# that warning, so it does not reach the stderr of scripts and hooks and worry the agent (D-039).
warnings.filterwarnings(
    "ignore",
    message="Dataset has no geotransform, gcps, or rpcs",
    category=rasterio.errors.NotGeoreferencedWarning,
    module=r"rasterio\.warp",
)

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"

# Sentinel-2 L2A scene classification (the SCL band), and the classes that hide the ground.
SCL_CLASSES = {
    0: "no data",
    1: "saturated or defective",
    2: "dark area or topographic shadow",
    3: "cloud shadow",
    4: "vegetation",
    5: "not vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud, medium probability",
    9: "cloud, high probability",
    10: "thin cirrus",
    11: "snow or ice",
}
SCL_OBSCURED = frozenset({0, 1, 3, 8, 9, 10})
SEARCH_KEY = "loci:search"
BOA_APPLIED = "earthsearch:boa_offset_applied"
GROUPBY = "solar_day"
CHUNKS: dict[str, int | Literal["auto"]] = {"x": 2048, "y": 2048}
RESAMPLING = ("nearest", "bilinear", "cubic", "average", "mode", "min", "max", "median")
FRAME_COLUMNS = ["id", "datetime", "collection", "platform", "cloud_cover", "grid", "epsg"]


def search_items(
    *,
    catalog: str,
    collection: str,
    bbox: Bounds | None,
    intersects: dict[str, Any] | None,
    datetime_range: str,
    max_cloud: float | None,
    limit: int,
) -> tuple[list[pystac.Item], int | None]:
    """Items matching the search (at most `limit`), and how many matched in all if known."""
    try:
        client = pystac_client.Client.open(catalog)
    except (pystac_client.exceptions.APIError, OSError) as e:
        raise LociError(f"Cannot open the STAC catalogue {catalog!r}: {e}") from e
    try:
        client.get_collection(collection)
    except (pystac_client.exceptions.APIError, OSError) as e:
        known = ", ".join(c.id for c in islice(client.get_collections(), 12))
        raise LociError(
            f"No collection {collection!r} in {catalog}.", hint=f"Collections include: {known}."
        ) from e
    query = {"eo:cloud_cover": {"lt": max_cloud}} if max_cloud is not None else None
    search = client.search(
        collections=[collection],
        bbox=list(bbox) if bbox is not None else None,
        intersects=intersects,
        datetime=datetime_range,
        query=query,
        max_items=limit,
    )
    items = list(search.items())
    try:
        matched = search.matched()
    except pystac_client.exceptions.APIError:
        matched = None
    return items, matched


def save_items(items: Sequence[pystac.Item], path: Path, search: dict[str, Any]) -> None:
    collection = pystac.ItemCollection(items).to_dict()
    collection[SEARCH_KEY] = search
    path.write_text(json.dumps(collection))


def read_items(uri: str) -> tuple[list[pystac.Item], dict[str, Any]]:
    """Items from a saved FeatureCollection, and the search that produced them."""
    try:
        data = json.loads(Path(uri).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise LoadError(f"Cannot read STAC items from {uri!r}: {e}") from e
    items = [pystac.Item.from_dict(f) for f in data.get("features", [])]
    return items, data.get(SEARCH_KEY, {})


def item_epsg(item: pystac.Item) -> int | None:
    if (epsg := item.properties.get("proj:epsg")) is not None:
        return int(epsg)
    code = item.properties.get("proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:"):
        return int(code.split(":", 1)[1])
    return None


def items_frame(items: Sequence[pystac.Item]) -> gpd.GeoDataFrame:
    """One row per item: footprint, UTC acquisition time, cloud cover, grid tile and CRS."""
    rows = [
        {
            "id": item.id,
            "datetime": item.datetime or item.properties.get("start_datetime"),
            "collection": item.collection_id,
            "platform": item.properties.get("platform"),
            "cloud_cover": item.properties.get("eo:cloud_cover"),
            "grid": item.properties.get("grid:code"),
            "epsg": item_epsg(item),
        }
        for item in items
    ]
    df = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert(None)
    df["cloud_cover"] = pd.to_numeric(df["cloud_cover"])
    df["epsg"] = df["epsg"].astype("Int64")
    geometry = [shape(item.geometry) if item.geometry else None for item in items]
    return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")


def read_stac_items(uri: str, fmt: str, options: ReadOptions) -> ReadResult:
    items, _ = read_items(uri)
    return ReadResult(items_frame(items), "vector", fmt, options, "file")


def loadable_bands(item: pystac.Item) -> list[str]:
    """Asset keys that hold a single band of GeoTIFF pixels."""
    bands = []
    for key, asset in item.assets.items():
        raster = asset.extra_fields.get("raster:bands")
        tiff = asset.media_type is None or "tiff" in asset.media_type
        if tiff and (raster is None or len(raster) == 1) and "data" in (asset.roles or ["data"]):
            bands.append(key)
    return bands


def load_stac(uri: str, fmt: str, options: ReadOptions) -> ReadResult:
    """Load the pixels of saved STAC items as a lazy (time, y, x) Dataset.

    Nothing is resampled or reprojected unless the options ask for it: mixed native resolutions
    or mixed CRSs are errors until resolution= or output_crs= is chosen, and any resampling or
    reprojection is noted. Scale and offset are attached to bands when every item agrees.
    drop_offset=True leaves declared offsets out, with a note, and a warning for items that do not
    say their offset was already applied; "auto" decides from the items (see _offset_choice).
    """
    items, search = read_items(uri)
    if not items:
        raise LoadError(
            "The search found no items, so there is nothing to load.",
            hint="Search again with wider dates, a higher max_cloud or a larger area.",
        )
    available = loadable_bands(items[0])
    bands = _choose_bands(options.bands, available)
    native = {band: _native_resolution(items[0], band) for band in bands}
    crs, reprojected = _output_crs(items, options.output_crs)
    resolution = _resolution(options.resolution, native, reprojected)
    resampling = options.resampling or "nearest"
    if resampling not in RESAMPLING:
        raise LoadError(f"Unknown resampling {resampling!r}.", hint=f"Use one of {RESAMPLING}.")
    bbox = options.bbox or search.get("bbox") or _union_bbox(items)

    try:
        ds = odc.stac.load(
            items,
            bands=bands,
            crs=crs_to_text(crs),
            resolution=resolution,
            bbox=tuple(bbox),
            groupby=GROUPBY,
            resampling=resampling,
            chunks=CHUNKS,
            fail_on_error=True,
        )
    except (ValueError, rasterio.errors.RasterioError, OSError) as e:
        raise LoadError(f"Cannot load the STAC items: {e}") from e

    automatic = options.drop_offset == "auto"
    drop_offset = _offset_choice(items, bands) if automatic else bool(options.drop_offset)
    offset_warnings, notes = _offset_checks(items, bands, drop_offset, automatic=automatic)
    warnings = _apply_scaling(ds, items, bands, drop_offset=drop_offset) + offset_warnings
    resampled = {b: r for b, r in native.items() if r != resolution}
    if resampled and not reprojected:
        listing = ", ".join(f"{b} {r:g}" for b, r in resampled.items())
        notes.append(f"Resampled to {resolution:g} from native ({listing}) with {resampling}.")
    if reprojected:
        notes.append(
            f"{reprojected} of {len(items)} items reprojected to {crs_to_text(crs)} at "
            f"{resolution:g} with {resampling}."
        )
    if ds.sizes["time"] < len(items):
        notes.append(f"{len(items)} items grouped by solar day into {ds.sizes['time']} time steps.")
    resolved = options.model_copy(
        update={
            "bands": bands,
            "resolution": resolution,
            "output_crs": crs_to_text(crs),
            "bbox": tuple(bbox),
            "resampling": resampling,
            # The decision, not "auto": a record reopens the same way whatever the items say.
            "drop_offset": drop_offset if automatic else options.drop_offset,
        }
    )
    crs_source: CrsSource = "argument" if reprojected else "file"
    return ReadResult(ds, "raster", fmt, resolved, crs_source, warnings, notes)


def _choose_bands(requested: list[str] | None, available: list[str]) -> list[str]:
    if not requested:
        raise LoadError(
            "Choose the bands to load with bands=.", hint=f"Bands: {', '.join(available)}."
        )
    unknown = [b for b in requested if b not in available]
    if unknown:
        raise LoadError(f"Unknown band(s) {unknown}.", hint=f"Bands: {', '.join(available)}.")
    return list(requested)


def _native_resolution(item: pystac.Item, band: str) -> float:
    asset = item.assets[band].extra_fields
    raster = (asset.get("raster:bands") or [{}])[0]
    for value in (raster.get("spatial_resolution"), asset.get("gsd")):
        if value is not None:
            return float(value)
    transform = asset.get("proj:transform")
    if transform:
        return abs(float(transform[0]))
    raise LoadError(f"Band {band!r} does not state its resolution.", hint="Pass resolution=.")


def _output_crs(items: Sequence[pystac.Item], requested: str | None) -> tuple[CRS, int]:
    """The output CRS, and how many items will be reprojected into it."""
    counts = Counter(item_epsg(item) for item in items)
    if requested is not None:
        crs = parse_crs(requested)
        target = crs.to_epsg()
        return crs, sum(n for epsg, n in counts.items() if epsg != target)
    if len(counts) == 1 and None not in counts:
        (epsg,) = counts
        assert epsg is not None
        return CRS.from_epsg(epsg), 0
    listing = ", ".join(f"EPSG:{e}: {n}" if e else f"unknown: {n}" for e, n in counts.items())
    raise LoadError(
        f"The items are in several CRSs ({listing}); choose one with output_crs=.",
        hint="Items in other CRSs are then reprojected, and the result notes it.",
    )


def _resolution(requested: float | None, native: dict[str, float], reprojected: int) -> float:
    if requested is not None:
        if requested <= 0:
            raise LoadError("resolution must be positive.")
        return float(requested)
    if reprojected:
        raise LoadError("Reprojected loads need an explicit resolution= in the output CRS's units.")
    resolutions = set(native.values())
    if len(resolutions) > 1:
        listing = ", ".join(f"{b} {r:g}" for b, r in native.items())
        raise LoadError(
            f"The bands have different native resolutions ({listing}); choose one with "
            "resolution=.",
            hint="Bands at other resolutions are then resampled with resampling= (default "
            "nearest), and the result notes it.",
        )
    return resolutions.pop()


def _apply_scaling(
    ds: Any, items: Sequence[pystac.Item], bands: list[str], *, drop_offset: bool = False
) -> list[str]:
    """Attach scale_factor/add_offset when all items agree; otherwise warn and leave raw.

    With drop_offset, declared offsets are ignored and only the scale is attached.
    """
    warnings = []
    for band in bands:
        pairs = {
            (scale, 0.0 if drop_offset else offset)
            for scale, offset in (_scale_offset(item, band) for item in items)
        }
        if len(pairs) > 1:
            listing = ", ".join(f"{s:g}/{o:g}" for s, o in sorted(pairs))
            warnings.append(
                f"Items declare different scale/offset for band '{band}' ({listing}), for "
                "example after a change of processing baseline. Values are left unscaled and "
                "are not comparable across those items until harmonised."
            )
            continue
        scale, offset = pairs.pop()
        if scale != 1.0:
            ds[band].attrs["scale_factor"] = scale
        if offset != 0.0:
            ds[band].attrs["add_offset"] = offset
    return warnings


def _offset_choice(items: Sequence[pystac.Item], bands: list[str]) -> bool:
    """Whether to leave declared offsets out, decided from the items (drop_offset="auto").

    Earth Search marks items whose stored values already include the offset with
    earthsearch:boa_offset_applied = true, yet still declares the offset (D-026). Left out when
    every item is marked, applied when none is. When only some are, no one choice suits them all:
    that is an error until drop_offset= is given, like mixed resolutions without resolution=.
    """
    if not any(_scale_offset(i, b)[1] != 0 for i in items for b in bands):
        return False
    marked = sum(i.properties.get(BOA_APPLIED) is True for i in items)
    if marked in (0, len(items)):
        return marked == len(items)
    raise LoadError(
        f"{marked} of {len(items)} items are marked {BOA_APPLIED} = true (their stored values "
        f"already include the declared offset) and {len(items) - marked} "
        f"{'is' if len(items) - marked == 1 else 'are'} not, so no one offset suits them all.",
        hint="Load them separately, for example over narrower dates, or choose for all of them: "
        "drop_offset=True leaves the offset out (the unmarked items come out too high by it), "
        "drop_offset=False applies it (the marked items count it twice).",
    )


def _offset_checks(
    items: Sequence[pystac.Item], bands: list[str], drop_offset: bool, *, automatic: bool = False
) -> tuple[list[str], list[str]]:
    """Warnings and notes about declared offsets.

    Applying a declared offset to items marked earthsearch:boa_offset_applied = true counts it
    twice; dropping it from items without the mark removes a real offset. Both are reported, and
    an automatic choice says why it was made.
    """
    applied = [i for i in items if i.properties.get(BOA_APPLIED) is True]
    with_offset = sorted({b for i in items for b in bands if _scale_offset(i, b)[1] != 0})
    if automatic:
        if not drop_offset:
            return [], []
        offsets = sorted({_scale_offset(i, b)[1] for i in items for b in with_offset} - {0.0})
        return [], [
            f"Declared offset {', '.join(f'{o:g}' for o in offsets)} left out of {with_offset}: "
            f"every item is marked {BOA_APPLIED} = true, so its stored values already include "
            "it. Physical value = stored * scale; pass drop_offset=False to apply it anyway."
        ]
    if not drop_offset:
        conflicting = sorted({b for i in applied for b in bands if _scale_offset(i, b)[1] != 0})
        if not conflicting:
            return [], []
        return [
            f"{len(applied)} of {len(items)} items {'is' if len(applied) == 1 else 'are'} "
            f"marked {BOA_APPLIED} = true, which says "
            f"the offset was already applied to their stored values, yet they still declare an "
            f"offset for {conflicting}. If both hold, the offset is counted twice: check physical "
            "values (validate with variables={band: 'reflectance'}) and reload with "
            "drop_offset=True if they are too low."
        ], []
    if not with_offset:
        return [], ["drop_offset=True changed nothing: no loaded band declares an offset."]
    offsets = sorted({_scale_offset(i, b)[1] for i in items for b in with_offset} - {0.0})
    listing = ", ".join(f"{o:g}" for o in offsets)
    note = (
        f"Declared offset {listing} not applied to {with_offset} (drop_offset=True): physical "
        "value = stored * scale."
    )
    unmarked = len(items) - len(applied)
    if not unmarked:
        return [], [note]
    return [
        f"drop_offset=True, but {unmarked} of {len(items)} items "
        f"{'is' if unmarked == 1 else 'are'} not marked {BOA_APPLIED} = true, so their stored "
        "values may still need the offset and are now too high by it. "
        "Check them, or load them separately without drop_offset."
    ], [note]


def _scale_offset(item: pystac.Item, band: str) -> tuple[float, float]:
    raster = (item.assets[band].extra_fields.get("raster:bands") or [{}])[0]
    return float(raster.get("scale", 1.0)), float(raster.get("offset", 0.0))


def _union_bbox(items: Sequence[pystac.Item]) -> Bounds:
    boxes = [item.bbox for item in items if item.bbox]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
