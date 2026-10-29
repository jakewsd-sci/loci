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

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Kind = Literal["raster", "vector"]
"""The two data kinds. Point data is a vector kind with Point geometries."""

CrsSource = Literal["file", "argument", "assumed"]
"""Where a dataset's CRS came from: declared by the source, given by the caller, or assumed by
loci (always accompanied by a warning)."""

StatsMethod = Literal["exact", "overview", "strided"]
"""How raster statistics were computed: from every pixel, from an overview (reduced-resolution
copy stored in the file), or from every Nth pixel along each axis."""

CheckStatus = Literal["pass", "warn", "fail"]
"""Outcome of a check. fail: results built on this data are likely wrong. warn: they may be
biased or need a decision; look before relying on them. pass: the check found nothing."""

Bounds = tuple[float, float, float, float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_inf_nan="strings")


class ReadOptions(_Model):
    """How a source was read. Stored with each data_ref so any process can reopen it identically.

    layer, x_col, y_col and crs apply to files; bands, resolution, bbox, output_crs, resampling
    and drop_offset to pixels loaded from STAC items.
    """

    layer: str | None = None
    x_col: str | None = None
    y_col: str | None = None
    crs: str | None = None
    bands: list[str] | None = None
    resolution: float | None = None
    bbox: Bounds | None = None
    output_crs: str | None = None
    resampling: str | None = None
    drop_offset: bool | None = None


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


class MonthCount(_Model):
    month: str
    items: int
    days: int
    median_cloud_cover: float | None = None


class ItemRow(_Model):
    id: str
    datetime: str
    cloud_cover: float | None = None
    grid: str | None = None


class StacSearchResult(_Model):
    """What `stac_search` returns: a data_ref for the items plus a compact account of them.

    The items are a vector layer of footprints, so inspect, validate and preview work on the
    data_ref; load(data_ref, bands=...) loads their pixels.
    """

    data_ref: str
    catalog: str
    collection: str
    bbox: Bounds
    start: str
    end: str
    max_cloud: float | None
    items: int
    matched: int | None
    first: str | None
    last: str | None
    days: int
    cloud_cover: NumericStats | None
    crs: dict[str, int]
    grids: dict[str, int]
    by_month: list[MonthCount]
    months_omitted: int = 0
    sample: list[ItemRow]
    bands: list[str]
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PreviewResult(_Model):
    """What `preview` returns: the path of a small PNG and what it shows."""

    data_ref: str
    path: str
    width: int
    height: int
    shows: str
    bands: list[str] | None = None
    column: str | None = None
    time: str | None = None
    stretch: tuple[float, float] | None = None
    extent: Extent
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CheckResult(_Model):
    """One geospatial check on one or more inputs.

    `check` is a stable identifier (e.g. "crs_defined"). `details` holds the measured values the
    status rests on. `hint` says what to do about a warn or fail.
    """

    check: str
    status: CheckStatus
    data_refs: list[str]
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)
    hint: str | None = None


class ValidationReport(_Model):
    """What `validate` returns. `status` is the worst status among the checks."""

    status: CheckStatus
    data_refs: list[str]
    counts: dict[CheckStatus, int]
    checks: list[CheckResult]


ProducedBy = Literal["load", "stac_search", "run"]


class SourceInput(_Model):
    """Something an output was made from: a data_ref, a file or URL, and any STAC items."""

    name: str | None = None
    data_ref: str | None = None
    uri: str | None = None
    stac_items: list[str] = Field(default_factory=list)


class Provenance(_Model):
    """How an output came to be. One per data_ref, plus one beside every file `run` writes.

    `call` is the tool call as made. For run outputs, `script` is the exact code that ran and
    `code_sha256` its hash. `software` holds the Python, loci (with a hash of its code), GDAL and
    key package versions. `validation` is the latest report from validate on this output.
    """

    data_ref: str | None = None
    path: str | None = None
    sha256: str | None = None
    created_at: str
    produced_by: ProducedBy
    call: dict[str, JsonValue]
    run_id: str | None = None
    script: str | None = None
    code_sha256: str | None = None
    inputs: list[SourceInput] = Field(default_factory=list)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    software: dict[str, str] = Field(default_factory=dict)
    validation: ValidationReport | None = None
    validated_at: str | None = None


RunStatus = Literal["ok", "error", "timeout"]


class RunOutput(_Model):
    """A file a run wrote. Spatial files become data_refs and are validated on the spot."""

    file: str
    data_ref: str | None = None
    kind: Kind | None = None
    validation: CheckStatus | None = None
    problems: list[str] = Field(default_factory=list)
    note: str | None = None


class RunResult(_Model):
    """What `run` returns. `input_checks` compares the declared inputs with each other (same
    CRS, overlapping extents, aligned grids) when there are two or more; the run goes ahead
    whatever it finds.
    """

    run_id: str
    status: RunStatus
    exit_code: int | None
    seconds: float
    run_dir: str
    stdout: str
    stderr: str
    outputs: list[RunOutput] = Field(default_factory=list)
    input_checks: ValidationReport | None = None
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LineageStep(_Model):
    """One output in a lineage: a data_ref, or a file a run wrote that is not a data_ref."""

    data_ref: str | None = None
    file: str | None = None
    produced_by: ProducedBy | None
    created_at: str | None = None
    description: str
    inputs: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    stac_items: int = 0
    validation: CheckStatus | None = None


class LineageResult(_Model):
    """What `lineage` returns: how a data_ref was made, back to its original sources.

    `steps` starts with the output itself and lists each ancestor once, nearest first.
    `provenance` is the output's own record, without its validation report and with STAC item
    lists shortened; the full record is at `record_path`.
    """

    data_ref: str | None = None
    file: str | None = None
    provenance: Provenance | None
    record_path: str | None
    steps: list[LineageStep]
    original_sources: list[str]
    stac_item_count: int
    stac_items: list[str]
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
