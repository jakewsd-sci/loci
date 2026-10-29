"""A demo project with exactly known answers, which `loci demo` writes and opens in the app.

Each question hides a trap that makes a careless answer wrong, as in the benchmarks (M6):
layers in different CRSs, areas measured in degrees, a point table with no stated CRS, and
slope on a grid in degrees. ANSWERS.md gives the exact answers, and what a trustworthy answer
deals with on the way, for whoever runs the demo.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loci import synthetic as syn

MARKER = ".loci-demo"  # marks a folder as a demo project, so the app offers its questions


@dataclass(frozen=True)
class Question:
    text: str  # what to ask the agent
    answer: str  # the exact answer
    trap: str  # what a trustworthy answer deals with


QUESTIONS = [
    Question(
        "data/dem.tif is an elevation model and data/catchments.geojson outlines two "
        "catchments, west and east. How much higher is the east catchment, on average?",
        f"{syn.DEM_EAST_MEAN - syn.DEM_WEST_MEAN:g} m (east {syn.DEM_EAST_MEAN:g} m, west "
        f"{syn.DEM_WEST_MEAN:g} m)",
        "The layers are in different CRSs (UTM and WGS 84), and the DEM has nodata in one "
        "catchment.",
    ),
    Question(
        "How many hectares of forest does data/landuse.geojson hold?",
        f"{2 * syn.POLY_AREA / 10_000:g} ha",
        "The polygons are in degrees, so areas must be measured on the ground, not in square "
        "degrees or Web Mercator.",
    ),
    Question(
        "data/sites.csv lists field sites, recorded in WGS 84 / UTM zone 33N. What is the mean "
        "NDVI at the sites, from data/ndvi.tif?",
        f"{syn.SAMPLE_NDVI_MEAN:g}, over the 8 sites the map covers",
        "The CSV does not state its CRS, and two of the ten sites lie off the raster.",
    ),
    Question(
        "What percentage of data/terrain.tif is steeper than 15 degrees?",
        f"{syn.SLOPE_STEEP_PERCENT:g}%",
        "The grid is in degrees and the heights in metres: slope needs both in metres.",
    ),
]
# Real data needs the internet; it has no known answer, so it is offered but not scored.
SATELLITE_QUESTION = (
    "Find the clearest Sentinel-2 scene of Sydney Harbour from June 2024 and map its NDVI"
)

FILES: dict[str, Callable[[Path], Path]] = {
    "data/dem.tif": syn.write_catchment_dem,
    "data/catchments.geojson": syn.write_catchments,
    "data/landuse.geojson": syn.write_polygons,
    "data/sites.csv": syn.write_sample_sites_csv,
    "data/ndvi.tif": syn.write_ndvi_raster,
    "data/terrain.tif": syn.write_two_slope_dem,
}


def is_demo(folder: Path) -> bool:
    return (folder / MARKER).exists()


def suggestions(folder: Path) -> list[str]:
    """The questions the app offers in a demo project; none elsewhere."""
    return [q.text for q in QUESTIONS] + [SATELLITE_QUESTION] if is_demo(folder) else []


def write_demo(folder: Path) -> list[Path]:
    """Write the demo's data, README and answers into `folder`; files already there are kept."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, write in FILES.items():
        path = folder / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(write(path))
    for name, text in ((MARKER, ""), ("README.md", _readme()), ("ANSWERS.md", _answers())):
        path = folder / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written


def _readme() -> str:
    asked = "\n".join(f"{i}. {q.text}" for i, q in enumerate(QUESTIONS, 1))
    return f"""# loci demo

A small project for trying loci. Ask the agent these in the app, where they are also offered
as suggestions:

{asked}
{len(QUESTIONS) + 1}. {SATELLITE_QUESTION} (needs the internet)

The data in `data/` are synthetic, made so that the exact answers are known (see ANSWERS.md,
for whoever runs the demo). Each question hides a trap that a careless analysis falls into:
watch the automatic checks catch it, read the scripts the agent writes in `analysis/`, and
open Lineage to see how each result was made.

To work with your own data, drag files onto the map, or open their folder with
`loci open <folder>`.
"""


def _answers() -> str:
    rows = "\n".join(f"| {q.text} | {q.answer} | {q.trap} |" for q in QUESTIONS)
    return f"""# Answers

Exact, because the data were made to have them. A trustworthy answer also deals with the trap.

| Question | Answer | Trap |
| --- | --- | --- |
{rows}
"""
