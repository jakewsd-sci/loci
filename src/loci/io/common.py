"""Shared pieces for readers: the read result and the CRS rule."""

from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import xarray as xr
from pyproj import CRS
from pyproj.exceptions import CRSError

from loci.errors import LoadError
from loci.models import CrsSource, Kind, ReadOptions

# A loaded dataset: rasters are xarray Datasets, vectors (points included) GeoDataFrames.
type Data = xr.Dataset | gpd.GeoDataFrame


@dataclass
class ReadResult:
    """A dataset plus the facts about how it was read.

    `options` are the resolved options (detected columns, chosen layer, assigned CRS): passing them
    back to the reader reopens the same data without repeating any detection.
    """

    data: Data
    kind: Kind
    format: str
    options: ReadOptions
    crs_source: CrsSource | None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_crs(text: str) -> CRS:
    try:
        return CRS.from_user_input(text)
    except CRSError as e:
        raise LoadError(
            f"Unrecognised CRS {text!r}.",
            hint="Use an EPSG code such as 'EPSG:4326', or WKT or PROJ text.",
        ) from e


def crs_to_text(crs: CRS) -> str:
    """'EPSG:<code>' when the CRS has one, otherwise WKT."""
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_wkt()


def resolve_crs(
    declared: Any, requested: str | None, *, what: str
) -> tuple[CRS | None, CrsSource | None, list[str]]:
    """Combine the CRS a source declares with the one the caller asked for.

    `requested` only fills in a missing CRS. It never overrides a declared one and never
    reprojects: a conflict is an error, because silently relabelling coordinates moves the data.
    Returns the CRS, where it came from, and any warnings.
    """
    if requested is None:
        if declared is None:
            return (
                None,
                None,
                [
                    f"{what} declares no CRS, so its coordinates cannot be placed on Earth or "
                    "combined with other data. Pass crs= if you know it."
                ],
            )
        return CRS.from_user_input(declared), "file", []

    wanted = parse_crs(requested)
    if declared is None:
        return wanted, "argument", []
    actual = CRS.from_user_input(declared)
    if actual.equals(wanted, ignore_axis_order=True):
        return actual, "file", []
    raise LoadError(
        f"{what} declares CRS {actual.name!r}, but crs={requested!r} was given.",
        hint="crs= only assigns a CRS to sources that declare none; it never reprojects. Omit "
        "crs= to use the declared CRS, and reproject as an explicit step if needed.",
    )
