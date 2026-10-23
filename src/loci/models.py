"""Result models returned by loci tools.

These models are the stable interface between the engine and its clients: the MCP server now, a
map-based UI later. Change them deliberately and record why in docs/DECISIONS.md.

Conventions:
- Bounds are (minx, miny, maxx, maxy). In WGS 84 that is (west, south, east, north) in degrees.
- Statistics are rounded to 6 significant figures and coordinates to 6 decimal places; full
  precision lives in the data, not the summary.
- None means "not applicable" or "not computed", never zero. NaN and infinity serialise as the
  strings "NaN" and "Infinity", so a NaN nodata value is not mistaken for "no nodata".
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["raster", "vector"]
"""The two data kinds. Point data is a vector kind with Point geometries."""

CrsSource = Literal["file", "argument", "assumed"]
"""Where a dataset's CRS came from: declared by the source, given by the caller, or assumed by
loci (always accompanied by a warning)."""

StatsMethod = Literal["exact", "overview", "strided"]
"""How raster statistics were computed: from every pixel, from an overview (reduced-resolution
copy stored in the file), or from every Nth pixel along each axis."""

Bounds = tuple[float, float, float, float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_inf_nan="strings")


class ReadOptions(_Model):
    """How a source was read. Stored with each data_ref so any process can reopen it identically."""

    layer: str | None = None
    x_col: str | None = None
    y_col: str | None = None
    crs: str | None = None


class CRSInfo(_Model):
    epsg: int | None
    name: str
    is_geographic: bool
    units: str | None


class Extent(_Model):
    bounds: Bounds
    bounds_wgs84: Bounds | None = None


class BandStats(_Model):
    """Statistics over valid pixels (not nodata, not NaN). Values are None if no pixel is valid."""

    min: float | None
    max: float | None
    mean: float | None
    std: float | None
    p2: float | None
    p98: float | None
    valid_fraction: float


class BandSummary(_Model):
    """One band. Values and stats are as stored; physical value = stored * scale + offset.

    scale and offset are None when the file declares none (equivalent to 1 and 0).
    """

    name: str
    dtype: str
    nodata: float | None
    scale: float | None = None
    offset: float | None = None
    stats: BandStats | None = None


class TimeCoverage(_Model):
    start: str
    end: str
    steps: int
    min_step_days: float | None = None
    median_step_days: float | None = None
    max_step_days: float | None = None


class RasterSummary(_Model):
    kind: Literal["raster"] = "raster"
    crs: CRSInfo | None
    crs_source: CrsSource | None = None
    extent: Extent
    resolution: tuple[float, float]
    dims: dict[str, int]
    time: TimeCoverage | None = None
    bands: list[BandSummary]
    bands_omitted: int = 0
    stats_method: StatsMethod | None = None
    stats_decimation: int | None = None


class NumericStats(_Model):
    min: float | None
    max: float | None
    mean: float | None


class CategoricalStats(_Model):
    n_unique: int
    top: list[tuple[str, int]]


class DatetimeStats(_Model):
    min: str | None
    max: str | None


class AttributeSummary(_Model):
    """One attribute column. At most one of numeric, categorical or datetime is set."""

    name: str
    dtype: str
    null_count: int
    numeric: NumericStats | None = None
    categorical: CategoricalStats | None = None
    datetime: DatetimeStats | None = None


class InvalidGeometry(_Model):
    row: int
    reason: str


class InvalidGeometries(_Model):
    count: int
    examples: list[InvalidGeometry] = Field(default_factory=list)


class PointsSummary(_Model):
    duplicate_locations: int
    has_z: bool


class VectorSummary(_Model):
    kind: Literal["vector"] = "vector"
    crs: CRSInfo | None
    crs_source: CrsSource | None = None
    extent: Extent | None
    feature_count: int
    geometry_types: dict[str, int]
    null_geometries: int
    empty_geometries: int
    invalid_geometries: InvalidGeometries
    attributes: list[AttributeSummary]
    attributes_omitted: int = 0
    points: PointsSummary | None = None


Summary = Annotated[RasterSummary | VectorSummary, Field(discriminator="kind")]


class RefSummary(_Model):
    """What `load` and `inspect` return: a data_ref plus a compact description of its data."""

    data_ref: str
    kind: Kind
    summary: Summary
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
