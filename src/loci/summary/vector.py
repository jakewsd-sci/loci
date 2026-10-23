"""Summaries of vector data held as GeoDataFrames, point data included."""

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pandas.api import types as ptypes

from loci.models import (
    AttributeSummary,
    CategoricalStats,
    CrsSource,
    DatetimeStats,
    InvalidGeometries,
    InvalidGeometry,
    NumericStats,
    PointsSummary,
    VectorSummary,
)
from loci.summary.common import (
    counted,
    crs_info,
    extent,
    iso,
    sig,
    take_within_budget,
    truncate,
)

MAX_ATTRIBUTES = 30
TOP_VALUES = 3
MAX_INVALID_EXAMPLES = 5


def summarise_vector(
    gdf: gpd.GeoDataFrame, *, crs_source: CrsSource | None = None
) -> VectorSummary:
    geoms = gdf.geometry.to_numpy()
    missing = shapely.is_missing(geoms)
    empty = shapely.is_empty(geoms) & ~missing
    present = ~missing & ~empty
    columns = [c for c in gdf.columns if c != gdf.geometry.name]
    attributes = take_within_budget(
        (attribute_summary(str(c), gdf[c]) for c in columns), max_items=MAX_ATTRIBUTES
    )
    types = {str(t): int(n) for t, n in gdf.geom_type[present].value_counts().items()}
    return VectorSummary(
        crs=crs_info(gdf.crs),
        crs_source=crs_source,
        extent=extent(shapely.total_bounds(geoms[present]), gdf.crs) if present.any() else None,
        feature_count=len(gdf),
        geometry_types=types,
        null_geometries=int(missing.sum()),
        empty_geometries=int(empty.sum()),
        invalid_geometries=invalid_geometries(geoms, present),
        attributes=attributes,
        attributes_omitted=len(columns) - len(attributes),
        points=_points_summary(geoms[present]) if set(types) == {"Point"} else None,
    )


def attribute_summary(name: str, values: pd.Series) -> AttributeSummary:
    """Type-appropriate statistics for one column, ignoring nulls."""
    present = values.dropna()
    any_present = len(present) > 0
    numeric = categorical = dates = None
    if ptypes.is_datetime64_any_dtype(values):
        dates = DatetimeStats(
            min=iso(present.min()) if any_present else None,
            max=iso(present.max()) if any_present else None,
        )
    elif ptypes.is_numeric_dtype(values) and not ptypes.is_bool_dtype(values):
        numbers = present.astype("float64")
        numeric = NumericStats(
            min=sig(numbers.min()) if any_present else None,
            max=sig(numbers.max()) if any_present else None,
            mean=sig(numbers.mean()) if any_present else None,
        )
    else:
        categorical = _categorical(present)
    return AttributeSummary(
        name=name,
        dtype=str(values.dtype),
        null_count=int(values.isna().sum()),
        numeric=numeric,
        categorical=categorical,
        datetime=dates,
    )


def vector_warnings(summary: VectorSummary) -> list[str]:
    """Geometry problems likely to break or bias analysis."""
    warnings = []
    if summary.null_geometries:
        has = counted(summary.null_geometries, "feature has", "features have")
        warnings.append(f"{has} no geometry.")
    if summary.empty_geometries:
        has = counted(summary.empty_geometries, "feature has", "features have")
        warnings.append(f"{has} an empty geometry.")
    invalid = summary.invalid_geometries
    if invalid.count:
        first = invalid.examples[0]
        has = counted(invalid.count, "feature has", "features have")
        warnings.append(
            f"{has} an invalid geometry (first: row {first.row}, {first.reason}). Overlays and "
            "areas involving them may fail or be wrong."
        )
    return warnings


def vector_notes(summary: VectorSummary) -> list[str]:
    notes = []
    if summary.points is not None and summary.points.duplicate_locations:
        shares = counted(summary.points.duplicate_locations, "point shares", "points share")
        notes.append(
            f"{shares} a location with another point; this matters for interpolation and "
            "cross-validation."
        )
    if summary.attributes_omitted:
        more = counted(summary.attributes_omitted, "more attribute is", "more attributes are")
        notes.append(f"{more} not listed; summaries show the first {len(summary.attributes)}.")
    return notes


def invalid_geometries(geoms: np.ndarray, present: np.ndarray) -> InvalidGeometries:
    """Count of invalid geometries among `present` ones, with the first few rows and reasons."""
    invalid = present.copy()
    invalid[present] = ~shapely.is_valid(geoms[present])
    rows = np.flatnonzero(invalid)
    examples = [
        InvalidGeometry(row=int(r), reason=truncate(str(shapely.is_valid_reason(geoms[r]))))
        for r in rows[:MAX_INVALID_EXAMPLES]
    ]
    return InvalidGeometries(count=len(rows), examples=examples)


def _points_summary(points: np.ndarray) -> PointsSummary:
    has_z = bool(shapely.has_z(points).any())
    coords = shapely.get_coordinates(points, include_z=has_z)
    unique = np.unique(coords, axis=0) if len(coords) else coords
    return PointsSummary(duplicate_locations=len(coords) - len(unique), has_z=has_z)


def _categorical(present: pd.Series) -> CategoricalStats:
    counts = present.astype(str).value_counts()
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_VALUES]
    return CategoricalStats(
        n_unique=int(present.nunique()),
        top=[(truncate(str(value)), int(count)) for value, count in ranked],
    )
