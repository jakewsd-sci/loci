# Decisions

One short entry per significant decision: context, choice, alternatives considered. Newest last.
Each entry notes the milestone and the date it was made.

## D-001 uv with a src layout and the uv_build backend (M0, 2026-10-23)

**Context.** The brief asks for a Python 3.11+ package managed with uv.

**Choice.** A `src/loci/` layout built with uv's own `uv_build` backend, with `uv.lock` committed.
The src layout means tests run against the installed package rather than whatever happens to be on
the path. `uv_build` is uv's default for libraries and needs no extra configuration for a pure
Python package.

**Alternatives.** A flat layout: simpler, but it can mask packaging mistakes. Hatchling or
setuptools: both fine, but each is one more tool to configure for no gain here.

## D-002 Python 3.12 as the minimum version (M0, 2026-10-23)

**Context.** The brief says Python 3.11+. The current releases of rasterio (1.5), rioxarray (0.23),
pyproj (3.8) and numpy (2.5) all require Python 3.12 or newer.

**Choice.** `requires-python = ">=3.12"`, with 3.12 as the local default (`.python-version`). CI
tests 3.12 and 3.13.

**Alternatives.** Supporting 3.11 would pin the project to older releases of the core geospatial
libraries, losing fixes and making it harder to adopt new ones.

## D-003 Three layers: core, tools, server (M0, 2026-10-23)

**Context.** The tools are first exposed over MCP to Claude Code. A UI will be built on the same
interfaces later. Everything must be testable without an MCP client.

**Choice.**
- Core modules (`io/`, `summary/`, `checks/`) work on xarray and geopandas objects and know nothing
  about data refs or MCP.
- Tool functions (`tools/`) take a workspace plus plain arguments and return pydantic models.
- `server.py` is a thin MCP adapter over `tools/`.

The tool signatures and result models are the stable contract.

**Alternatives.** Writing MCP tool handlers directly: less code at first, but it couples the logic
to one protocol and makes unit tests depend on the MCP SDK.

## D-004 ruff, mypy and pytest, with a `network` marker (M0, 2026-10-23)

**Context.** The brief asks for pytest and linting, and for small typed functions.

**Choice.**
- **ruff** for linting and formatting (rules E, F, W, I, UP, B, SIM, RUF; line length 100). It
  replaces flake8, isort and black with one fast tool.
- **mypy** over `src/` and `tests/` with untyped definitions disallowed, so every function signature
  is typed, tests included.
- **pytest** with `--import-mode=importlib` (no `__init__.py` files in `tests/`) and a `network`
  marker that is deselected by default. The normal suite therefore runs offline and
  deterministically, and `uv run pytest -m network` runs the real-data checks.

**Alternatives.** pyright: stricter and faster, but mypy is the more common default and handles the
untyped geospatial libraries more gracefully with per-module overrides. Full `mypy --strict`:
deferred, because much of the geospatial stack ships without type stubs.

## D-005 GitHub Actions CI with pinned versions (M0, 2026-10-23)

**Context.** Breakage should be caught on every push, not only on a local machine.

**Choice.** One workflow runs `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy` and
`pytest` on Python 3.12 and 3.13.
- Actions are pinned by commit SHA, following uv's GitHub integration guide.
- uv is pinned to the version used to write `uv.lock` (0.11.32), so CI checks the lockfile with the
  same resolver. Bumping uv is a one-line change.
- Network tests do not run in CI, which keeps it fast and free of flakes from remote services.

**Alternatives.** No CI: relies on remembering to run checks locally. Unpinned actions or uv:
convenient, but a release upstream can break CI with no change in this repository.

## D-006 Add dependencies in the milestone that first uses them (M0, 2026-10-23)

**Context.** The brief lists the core libraries and asks to be consulted before adding other
substantial ones.

**Choice.** Each dependency is added in the milestone that first imports it:
- M1: the raster and vector stack.
- M3: matplotlib, pystac-client and odc-stac.
- M5: the MCP SDK.
- M6: the terrain library, chosen there and recorded as its own decision. pysheds is the current
  candidate.

This keeps every milestone's environment honest about what it actually needs. Libraries outside
the brief's list are flagged before they are added. For M1 these are pyarrow, needed for the
brief's Parquet support, and pydantic, which the MCP SDK already depends on.

**Alternatives.** Declaring the whole stack up front: simpler `pyproject.toml` history, but unused
dependencies hide which parts of the stack each feature really needs.

## D-007 data_ref format: `{kind}-{8 hex}`, validated before use (M1, 2026-10-23)

**Context.** Tools hand data around by reference. Refs come back from the model, so they can be
mistyped or malformed.

**Choice.** Refs look like `raster-3f9a2c1b` or `vector-91be07aa`. They are random, readable, and
show their kind at a glance. Every ref is checked against a strict pattern before it becomes part
of a file path, which rules out path traversal and gives a clear error with recent refs as a hint.

**Alternatives.** Content hashes: they deduplicate, but hashing large or remote data is slow and
the refs are long. UUIDs: unique, but long and they don't show the kind.

## D-008 Project-level, disk-backed workspace (M1, 2026-10-23)

**Context.** The MCP server is long-lived, but `run` scripts (M4) and hooks (M5) are separate
processes that must resolve refs too. Lineage must survive server restarts.

**Choice.** One workspace per project at `.loci/` (gitignored), or `$LOCI_WORKSPACE`. Each ref is
one JSON record in `.loci/refs/`, written atomically. A record holds:
- the source URI;
- the resolved read options (detected columns, chosen layer, assigned CRS), so it can be reopened
  without repeating any detection;
- where the CRS came from, and the load warnings;
- the session that created it;
- a size and modification-time fingerprint for local files. `inspect` warns when that
  fingerprint no longer matches.

Opened data is cached in memory per process.

**Alternatives.** An in-memory registry: simpler, but other processes can't see it and it is lost
on restart. A per-session directory: refs from earlier sessions would be unresolvable. SQLite:
more machinery than a directory of small JSON files needs.

## D-009 Rasters are xarray Datasets with one variable per band, values kept raw (M1, 2026-10-23)

**Context.** Rasters come from single files now and from STAC (odc-stac) in M3. Analysis code
should see one representation.

**Choice.** An `xr.Dataset` with one data variable per band and dims `(y, x)` or `(time, y, x)`,
which matches odc-stac output. Bands take their names from the GeoTIFF band descriptions when
every band has a distinct one, and are `band_1…n` otherwise. Files open with
`mask_and_scale=False`, so dtype, nodata, scale and offset are reported as stored and any masking
is explicit.

Two rioxarray quirks shaped the code:
- Band descriptions are read through rasterio, because rioxarray drops them at overview levels
  (and whenever `overview_level=None` is passed).
- An `atexit` hook clears xarray's file cache. Otherwise cached handles are finalised during
  interpreter teardown and print `Error in sys.excepthook` to stderr, which matters once `run`
  captures stderr.

**Alternatives.** A `DataArray` with a `band` dimension: natural for single-file GeoTIFFs, but it
forces one dtype and one nodata value for all bands and differs from odc-stac output.

## D-010 Rasters open lazily; `load` returns a header, `inspect` computes statistics (M1, 2026-10-23)

**Context.** Loading a remote COG should be cheap, and pixel statistics can be expensive.

**Choice.** Rasters always open as dask-backed arrays, giving one code path for local files and
`https://`, `s3://` and `/vsi…` URLs. `load` returns the grid and band metadata without reading any
pixels, plus a note pointing to `inspect`. Vectors are read eagerly and summarised in full.
`GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` is set by default, so opening one remote file doesn't
list its directory first.

**Alternatives.** Statistics at load time: one call fewer, but a slow surprise for large remote
rasters. Eager reads for small local files: two code paths for little gain.

## D-011 Bounded, labelled raster statistics (M1, 2026-10-23)

**Context.** Statistics on a full Sentinel-2 band (120 million pixels) are too slow for an
interactive tool. Silently sampled statistics are hard to trust.

**Choice.** `inspect` reads at most `max_pixels` values per band (default 4 million).
- A larger raster uses the finest overview stored in the file that is coarse enough.
- If the file has no suitable overview, it falls back to a strided sample.
- The summary records `stats_method` (`exact`, `overview` or `strided`) and the decimation
  factor, and a note says that extremes and valid fractions are then approximate.

Valid pixels are those that are finite and not equal to nodata. Measured: the 5490×5490 SCL band of
a real scene is inspected from its 1/4 overview in under a second over HTTPS.

**Alternatives.** Approximate statistics stored in the GeoTIFF: often missing or stale. Exact
statistics with dask: correct, but unbounded in time and network traffic.

## D-012 Point data is a vector kind; the CRS rule for coordinate tables (M1, 2026-10-23)

**Context.** Field samples usually arrive as CSV with coordinate columns and often state no CRS.
Guessing silently undermines trust; refusing is needless friction for the commonest case.

**Choice.** CSV files, and Parquet files without GeoParquet metadata, become point GeoDataFrames
with every row kept.
- Coordinate columns are detected by name (lon/longitude/x/easting, lat/latitude/y/northing),
  case-insensitively. Ambiguity is an error asking for `x_col`/`y_col`.
- Rows with missing or non-numeric coordinates get a null geometry and are counted in a warning.
- **CRS rule (chosen by the user):** without `crs=`, EPSG:4326 is assumed only when the columns are
  named like lon/lat and every value is in range. `crs_source` is then `"assumed"` and a warning
  says so. In every other case the CRS stays undefined, with a warning.
- Point layers get a small `points` block counting duplicate locations, which matters for
  cross-validation.

**Alternatives.** A separate `points` kind: more surface area for no real difference in handling.
Never assuming a CRS: stricter, but it adds a reload to almost every field dataset.

## D-013 `crs=` assigns a missing CRS and never reprojects (M1, 2026-10-23)

**Context.** Mixing up "this data is in CRS X" with "convert this data to CRS X" is a classic
source of silently misplaced data.

**Choice.** For files, `crs=` fills in a CRS only when the source declares none. If the source
already declares a different one, `load` raises an error. For tables, `crs=` is the CRS of the
coordinate columns. Every result records `crs_source` (`file`, `argument` or `assumed`).
Reprojection, when it arrives, will be an explicit step with its own provenance.

**Alternatives.** Letting `crs=` override, as some libraries do: convenient, and a quiet way to move
data across the globe.

## D-014 Summaries capped by count and by bytes (M1, 2026-10-23)

**Context.** Summaries go into the model's context, so they must stay small whatever the data. A
count cap alone doesn't bound size: 30 long text columns can still produce 10 KB.

**Choice.**
- Floats are rounded to 6 significant figures and coordinates to 6 decimal places.
- String values are truncated to 40 characters, with at most 3 top values per attribute and at
  most 5 invalid-geometry examples.
- Band and attribute lists are capped at 20 and 30 items, and also by a 3 KB JSON budget.
  Items are added in order until the next would exceed the budget, and `bands_omitted` or
  `attributes_omitted` reports the rest.

Tests keep every fixture's full `RefSummary` under 4 KB. The 67-column fixture lists 21 attributes
in 3.7 KB.

**Alternatives.** A count cap only: simpler, but it doesn't actually guarantee a size.
Truncating the serialised JSON: would produce invalid or misleading output.

## D-015 pydantic result models; NaN serialised as a string (M1, 2026-10-23)

**Context.** Results need a stable, validated, JSON-ready schema that MCP and a UI can both use.

**Choice.** Frozen pydantic models in `loci/models.py` that reject unknown fields. pydantic is
already a dependency of the MCP SDK, and FastMCP turns pydantic return types into structured
output. NaN and infinity serialise as the strings `"NaN"` and `"Infinity"`, so a NaN nodata value
is not mistaken for `null` ("no nodata").

**Alternatives.** Dataclasses with hand-written JSON: no validation, and more code. TypedDicts: no
runtime validation.

## D-016 Synthetic fixtures live in the package and are generated, not committed (M1, 2026-10-23)

**Context.** Tests need data with exactly known answers, and the M6 benchmark needs the same
fixtures.

**Choice.** `loci.synthetic` writes small deterministic files and defines their known answers as
constants beside each writer (for example, the ramp raster's valid fraction of exactly 0.9).
`tests/test_synthetic.py` checks those answers with rasterio and geopandas directly, independent of
loci's own readers. Files are written to a pytest session temp directory, so no binaries go into
git.

**Alternatives.** Committed binary fixtures: opaque in diffs and easy to let drift from the
documented answers. Fixtures under `tests/`: the benchmark could not import them cleanly.

## D-017 Warnings are errors in tests (M1, 2026-10-23)

**Context.** Geospatial libraries signal real problems through warnings, such as pyogrio reporting
that a date field was written as a string. Ignoring them hides those problems.

**Choice.** pytest runs with `filterwarnings = error`. Known, harmless third-party warnings are
filtered by exact message: rioxarray uses affine's deprecated `*` operator internally. Tests that
deliberately provoke a warning filter it themselves.

**Alternatives.** Default warning handling: quieter, but it lets regressions through.
