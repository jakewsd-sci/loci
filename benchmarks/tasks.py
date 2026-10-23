"""The five benchmark tasks: questions with exactly known answers, each with a trap (D-041).

A task writes its data with loci.synthetic into a folder of its own; the first also starts the
workspace from a STAC search. Its prompt is what an agent is asked. Its reference solution answers
the question with loci's tools, as a careful agent would, and lists what the tools reported that
points at the trap, so the benchmark also shows where loci helps and where it is silent.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from loci import synthetic as syn
from loci.errors import LociError
from loci.models import RasterSummary, RefSummary, RunResult, VectorSummary
from loci.tools.load import load
from loci.tools.run import run
from loci.tools.stac_search import stac_search
from loci.tools.validate import validate
from loci.workspace import Workspace

SYNTHETIC_CATALOG = "synthetic catalogue (loci benchmark)"
ANSWER_FILE = "answer.json"

# Task 1: red reflectance 0.05, 0.06 and 0.07 under nir 0.2 gives NDVI 0.6, 0.538 and 0.481. Cloud
# covers the top 12 rows of the first scene and the top 24 of the last, so the per-pixel medians of
# clear observations are 0.538 (rows 0-11), 0.569 (rows 12-23) and 0.538 (rows 24-39).
NDVI_RED = (1500, 1600, 1700)
NDVI_CLOUD_ROWS = (12, 0, 24)
_NDVI = [(0.2 - r) / (0.2 + r) for r in (0.05, 0.06, 0.07)]
NDVI_ANSWER = (28 * _NDVI[1] + 12 * (_NDVI[0] + _NDVI[1]) / 2) / 40


@dataclass(frozen=True)
class Reference:
    """A reference solution's answer, and what loci reported along the way."""

    value: float
    flags: list[str]
    answer_file: Path


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    data: str
    trap: str
    prompt: str
    answer: float
    tolerance: float
    unit: str
    caveats: tuple[str, ...]
    setup: Callable[[Path, Workspace], dict[str, str]]
    reference: Callable[[Workspace, dict[str, str]], Reference]

    def question(self, names: dict[str, str]) -> str:
        return self.prompt.format(**names)

    def correct(self, value: float | None) -> bool:
        return value is not None and abs(value - self.answer) <= self.tolerance


def _answer(result: RunResult) -> tuple[float, Path]:
    """The value a reference script saved to answer.json."""
    if result.status != "ok":
        raise RuntimeError(f"Reference script failed ({result.status}):\n{result.stderr}")
    path = Path(result.run_dir, "outputs", ANSWER_FILE)
    return float(json.loads(path.read_text())["answer"]), path


def _not_passing(ws: Workspace, refs: list[str]) -> list[str]:
    report = validate(ws, refs)
    return [
        f"validate: {c.status} {c.check}: {c.message}" for c in report.checks if c.status != "pass"
    ]


def _crs_note(result: RefSummary) -> list[str]:
    summary = result.summary
    assert isinstance(summary, RasterSummary | VectorSummary)
    crs = summary.crs
    if crs is not None and crs.is_geographic:
        return [f"load: CRS {crs.name} (EPSG:{crs.epsg}) is geographic, in {crs.units}s"]
    return []


# 1. NDVI over a cloudy month


def _ndvi_setup(folder: Path, ws: Workspace) -> dict[str, str]:
    items = syn.stac_items(folder / "scenes", red=NDVI_RED, cloud_rows=NDVI_CLOUD_ROWS)

    def search_items(**_: Any) -> tuple[list[Any], int]:
        return items, len(items)

    # The real stac_search, with the catalogue simulated by the synthetic scenes.
    with patch("loci.tools.stac_search.search_items", search_items):
        found = stac_search(
            ws,
            bbox=list(items[0].bbox or []),
            start="2024-03-01",
            end="2024-03-31",
            catalog=SYNTHETIC_CATALOG,
        )
    return {"search": found.data_ref}


NDVI_SCRIPT = """
from loci import script

stack = script.read("stack")
clear = ~stack["scl"].isin([0, 1, 3, 8, 9, 10])  # no data, saturated, shadow, cloud, cirrus
ndvi = (stack["nir"] - stack["red"]) / (stack["nir"] + stack["red"])
medians = ndvi.where(clear).median("time")
script.save({"answer": float(medians.mean())}, "answer.json")
"""


def _ndvi_reference(ws: Workspace, names: dict[str, str]) -> Reference:
    flags = []
    try:
        load(ws, names["search"], bands=["red", "nir", "scl"])
    except LociError as e:
        flags.append(f"load: {e.message}")
    stack = load(ws, names["search"], bands=["red", "nir", "scl"], resolution=10)
    flags += [f"load: {note}" for note in stack.notes if note.startswith("Resampled")]
    value, path = _answer(run(ws, code=NDVI_SCRIPT, inputs={"stack": stack.data_ref}))
    return Reference(value, flags, path)


# 2. Elevation by catchment, layers in different CRSs


def _zonal_setup(folder: Path, ws: Workspace) -> dict[str, str]:
    return {
        "dem": str(syn.write_catchment_dem(folder / "dem.tif")),
        "catchments": str(syn.write_catchments(folder / "catchments.geojson")),
    }


ZONAL_SCRIPT = """
from loci import script

dem = script.read("dem")["elevation"]  # nodata is NaN
zones = script.read("catchments").to_crs(dem.rio.crs)  # move the vectors, not the pixels
means = {
    row.catchment: float(dem.rio.clip([row.geometry], drop=True).mean())
    for row in zones.itertuples()
}
script.save({"answer": means["east"] - means["west"], "means": means}, "answer.json")
"""


def _zonal_reference(ws: Workspace, names: dict[str, str]) -> Reference:
    dem, zones = load(ws, names["dem"]), load(ws, names["catchments"])
    flags = _not_passing(ws, [dem.data_ref, zones.data_ref])
    inputs = {"dem": dem.data_ref, "catchments": zones.data_ref}
    value, path = _answer(run(ws, code=ZONAL_SCRIPT, inputs=inputs))
    return Reference(value, flags, path)


# 3. Forest area from polygons in degrees


def _area_setup(folder: Path, ws: Workspace) -> dict[str, str]:
    return {"landuse": str(syn.write_polygons(folder / "landuse.geojson"))}


AREA_SCRIPT = """
from pyproj import Geod
from loci import script

parcels = script.read("landuse").to_crs("EPSG:4326")
geod = Geod(ellps="WGS84")
forest = parcels[parcels["landuse"] == "forest"]
square_metres = sum(abs(geod.geometry_area_perimeter(g)[0]) for g in forest.geometry)
script.save({"answer": square_metres / 10_000}, "answer.json")
"""


def _area_reference(ws: Workspace, names: dict[str, str]) -> Reference:
    landuse = load(ws, names["landuse"])
    value, path = _answer(run(ws, code=AREA_SCRIPT, inputs={"landuse": landuse.data_ref}))
    return Reference(value, _crs_note(landuse), path)


# 4. NDVI at sample sites, CRS not stated, two sites off the map


def _sites_setup(folder: Path, ws: Workspace) -> dict[str, str]:
    return {
        "sites": str(syn.write_sample_sites_csv(folder / "sites.csv")),
        "ndvi": str(syn.write_ndvi_raster(folder / "ndvi.tif")),
    }


SITES_SCRIPT = """
import xarray as xr
from loci import script

ndvi = script.read("ndvi")["ndvi"]
sites = script.read("sites")
left, bottom, right, top = ndvi.rio.bounds()
covered = sites.cx[left:right, bottom:top]  # nearest-pixel lookup would clamp the others
values = ndvi.sel(
    x=xr.DataArray(covered.geometry.x), y=xr.DataArray(covered.geometry.y), method="nearest"
)
script.save(
    {"answer": float(values.mean()), "sites": len(sites), "covered": len(covered)},
    "answer.json",
)
"""


def _sites_reference(ws: Workspace, names: dict[str, str]) -> Reference:
    flags = [f"load: {w}" for w in load(ws, names["sites"]).warnings]
    sites = load(ws, names["sites"], crs=syn.UTM33N)  # as the prompt says
    ndvi = load(ws, names["ndvi"])
    flags += _not_passing(ws, [sites.data_ref, ndvi.data_ref])
    inputs = {"sites": sites.data_ref, "ndvi": ndvi.data_ref}
    value, path = _answer(run(ws, code=SITES_SCRIPT, inputs=inputs))
    return Reference(value, flags, path)


# 5. Slope from a DEM on a grid in degrees


def _slope_setup(folder: Path, ws: Workspace) -> dict[str, str]:
    return {"dem": str(syn.write_two_slope_dem(folder / "terrain.tif"))}


SLOPE_SCRIPT = """
import numpy as np
from rasterio.enums import Resampling
from loci import script

dem = script.read("dem")["elevation"]
metric = dem.rio.reproject(
    dem.rio.estimate_utm_crs(), resolution=10, resampling=Resampling.bilinear, nodata=np.nan
)
rise_y, rise_x = np.gradient(metric.values.astype("float64"), 10, 10)
slope = np.degrees(np.arctan(np.hypot(rise_x, rise_y)))
valid = np.isfinite(slope)
script.save({"answer": float((slope[valid] > 15).mean() * 100)}, "answer.json")
"""


def _slope_reference(ws: Workspace, names: dict[str, str]) -> Reference:
    dem = load(ws, names["dem"])
    value, path = _answer(run(ws, code=SLOPE_SCRIPT, inputs={"dem": dem.data_ref}))
    return Reference(value, _crs_note(dem), path)


TASKS = [
    Task(
        id="cloudy-ndvi",
        title="NDVI over a cloudy month",
        data="raster time series from a STAC search (synthetic scenes)",
        trap="Cloud in two of three scenes, flagged only in the 20 m scene classification band.",
        prompt=(
            "In the loci workspace, {search} is a STAC search of a field: three Sentinel-2-like "
            "scenes from March 2024 with the bands red, nir and scl (scene classification). For "
            "each pixel, take the median NDVI of its cloud-free observations, then report the "
            "mean of those medians over the field."
        ),
        answer=NDVI_ANSWER,
        tolerance=0.001,
        unit="NDVI",
        caveats=(r"cloud", r"20 ?m|resampl|resolution"),
        setup=_ndvi_setup,
        reference=_ndvi_reference,
    ),
    Task(
        id="catchment-elevation",
        title="Elevation by catchment",
        data="raster DEM in UTM and catchment polygons in WGS 84",
        trap="The layers are in different CRSs, and the DEM has nodata in one catchment.",
        prompt=(
            "{dem} is a digital elevation model (heights in metres) and {catchments} outlines two "
            "catchments, west and east. On average, how much higher is the east catchment than "
            "the west? Report the mean elevation of the east catchment minus that of the west, in "
            "metres."
        ),
        answer=syn.DEM_EAST_MEAN - syn.DEM_WEST_MEAN,
        tolerance=0.5,
        unit="m",
        caveats=(r"CRS|coordinate|reproject|EPSG",),
        setup=_zonal_setup,
        reference=_zonal_reference,
    ),
    Task(
        id="forest-area",
        title="Forest area",
        data="land-use polygons in WGS 84 (degrees)",
        trap="Areas must be measured on the ground, not in square degrees or in Web Mercator.",
        prompt="{landuse} maps land use in four parcels. How many hectares of forest does it hold?",
        answer=2 * syn.POLY_AREA / 10_000,
        tolerance=0.01,
        unit="ha",
        caveats=(r"equal[- ]area|geodesic|ellipsoid|UTM|EPSG:\d+|project",),
        setup=_area_setup,
        reference=_area_reference,
    ),
    Task(
        id="ndvi-at-sites",
        title="NDVI at sample sites",
        data="point table without a stated CRS and an NDVI raster",
        trap="The CSV does not state its CRS, and two of ten sites lie off the raster.",
        prompt=(
            "{sites} lists field sample sites; the survey team recorded their positions in WGS 84 "
            "/ UTM zone 33N. {ndvi} is an NDVI map of the area. What is the mean NDVI at the "
            "sample sites?"
        ),
        answer=syn.SAMPLE_NDVI_MEAN,
        tolerance=0.001,
        unit="NDVI",
        caveats=(r"outside|not covered|beyond|off the|exclud|fall outside", r"32633|UTM"),
        setup=_sites_setup,
        reference=_sites_reference,
    ),
    Task(
        id="steep-terrain",
        title="Steep terrain",
        data="DEM with heights in metres on a grid in degrees",
        trap="Horizontal units are degrees and vertical units metres; slope needs both in metres.",
        prompt=(
            "{dem} is a digital elevation model with heights in metres. What percentage of its "
            "area is steeper than 15 degrees?"
        ),
        answer=syn.SLOPE_STEEP_PERCENT,
        tolerance=2.0,
        unit="%",
        caveats=(r"degree|geographic|4326", r"reproject|metre|meter|UTM|32633"),
        setup=_slope_setup,
        reference=_slope_reference,
    ),
]

TASKS_BY_ID = {task.id: task for task in TASKS}
