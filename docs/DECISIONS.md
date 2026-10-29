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

## D-018 validate returns a pass/warn/fail report with evidence (M2, 2026-10-23)

**Context.** Agents and hooks need to act on validation results, and scientists need to see why a
check concluded what it did.

**Choice.** `validate` returns a `ValidationReport`: an overall status (the worst of its checks),
counts, and one `CheckResult` per check. Each result has a stable id (`crs_defined`,
`valid_fraction`, …), a status, the data_refs it concerns, a one-sentence message, the measured
details it rests on, and a hint saying what to do whenever it is not a pass. The statuses mean:
- **fail:** results built on this data are likely wrong.
- **warn:** results may be biased or need a decision, so look before relying on them.
- **pass:** the check found nothing.

Data problems are reported, never raised. Only bad arguments raise errors.

**Alternatives.** Booleans per check: no room for "look at this". Free-text reports: hard for an
agent or a UI to act on.

## D-019 Several data_refs mean "these will be combined" (M2, 2026-10-23)

**Context.** The brief asks for CRS, extent and resolution consistency "across inputs that are
combined", but `validate` cannot see the operation that will combine them.

**Choice.** Passing several data_refs to one `validate` call states that they will be used
together. They are then also checked against each other: `crs_consistent`, `extents_overlap` and,
for two or more rasters, `resolution_consistent`. Different CRSs **fail**, because combining them
without an explicit reprojection misplaces data. Different resolutions or misaligned grids **warn**,
because resampling is legitimate once it is explicit and recorded. Extents compare bounding boxes in
the first input's CRS: disjoint fails, and partial coverage of the smaller by the larger warns.

**Alternatives.** An `operation=` argument naming the intended combination: more precise, but a
vocabulary of operations to maintain before any benchmark needs it.

## D-020 Valid-pixel fraction: below the threshold warns, none at all fails (M2, 2026-10-23)

**Context.** The brief asks for a valid-pixel fraction "above a configurable threshold". The right
threshold depends on the analysis. Clouds, coastlines and swath edges legitimately reduce coverage.

**Choice.** `min_valid_fraction` defaults to 0.5. A band below it warns. A band with no valid pixels
fails. For time stacks, every step is checked too, so two cloudy months warn even when the stack
as a whole is fine. The check reads the same bounded sample as `inspect` and says when that sample
is approximate.

**Alternatives.** Failing below the threshold: blocks too often on legitimately sparse data, which
would teach agents to ignore failures.

## D-021 Value ranges for declared variables, and normalised indices recognised by name (M2, 2026-10-23)

**Context.** "Values within a plausible range for the declared variable" needs a declaration
mechanism and a table of ranges.

**Choice.** `variables={"band_1": "reflectance"}` declares what a band or column holds. A small
registry gives each variable a range and says whether going outside it fails or warns:
- **fails:** normalised differences, fractions, slope and aspect, elevation and flow accumulation,
  whose ranges are hard limits;
- **warns:** reflectance and EVI, whose ranges are plausibility limits.

NDVI, NDWI, NDMI, NBR, SAVI and EVI are recognised from band or column names alone, and the result
says so. Ambiguous names (`slope`: degrees or percent?) are not guessed. Ranges apply to physical
values (stored × scale + offset). A declared name that matches no band, or an unknown variable, is an
error.

**Alternatives.** Custom `(min, max)` ranges per call: flexible, but nothing needs them yet.
Inferring more names: more automatic, but each guess is a chance to be confidently wrong.

## D-022 Area conservation against an explicit reference layer (M2, 2026-10-23)

**Context.** "Area conservation in overlays and dissolves" needs to know what the output should
conserve. For a dissolve that is the input's total area. For a land-use × catchment overlay it is each
catchment's area.

**Choice.** `area_reference=<data_ref>` names the layer to compare against. With `area_by=<column>`,
areas are compared per value of a column both layers share; otherwise totals are compared. Areas are
computed in m²: geodesic for geographic CRSs, and planar with unit conversion for projected ones.
Layers in different CRSs are not compared. The default tolerance is 0.01% relative. The result names
the worst group.

**Alternatives.** Inferring the reference from lineage (M4): attractive later. For now an explicit
reference makes the claim being tested visible.

## D-023 Temporal coverage judged against the median step (M2, 2026-10-23)

**Context.** Series arrive daily, every 5 days or monthly, and the check should not need to be told
which.

**Choice.** Steps are compared with the series' median step:
- a step longer than 1.5× the median is a gap;
- a step under 0.5× the median is uneven sampling;
- a repeated timestamp is flagged too.

Any of these warns, and the result lists up to three gaps, largest first. Rasters are checked along
`time`; vectors along a `time_column` the caller names. Steps are computed via `timedelta64[s]`,
because pandas 3 may store timestamps in microseconds; a unit bug found by the tests.

**Alternatives.** An expected interval argument: precise, but monthly steps vary from 28 to 31 days
anyway, and the median handles that.

## D-024 Search results are a vector layer of item footprints (M3, 2026-10-23)

**Context.** `stac_search` must return a `data_ref`, and the brief wants everything kept
general across data types.

**Choice.** The items are saved as a GeoJSON FeatureCollection at `.loci/stac/<data_ref>.json`, with
the search parameters under `loci:search` for provenance. The `data_ref` reads back as a
GeoDataFrame of footprints (id, UTC time, cloud cover, grid tile, EPSG). `inspect`, `validate`
(`time_column="datetime"`) and `preview` therefore work on search results unchanged. The tool's
result adds a per-month table, a sample of items, CRS and tile counts, and the loadable bands. It
warns when the limit truncates the search or nothing matched.

**Alternatives.** A separate "items" kind with its own summary: more code paths and nothing gained.
Keeping only item IDs: loading would need the catalogue again, and a moving catalogue breaks
reproducibility.

## D-025 load takes a search's data_ref; no silent resampling or reprojection (M3, 2026-10-23)

**Context.** odc-stac quietly resamples bands to the finest resolution and reprojects to whatever
CRS it is given. Both change the data.

**Choice.** `load(ws, <search data_ref>, bands=[...])` loads pixels lazily through odc-stac into a
`(time, y, x)` Dataset. The default extent is the search area. Items are grouped by solar day, so
neighbouring granules from one overpass merge into a single step.
- `bands=` is required. The error lists the available ones.
- Bands with different native resolutions are an error until `resolution=` is given.
- Items in several CRSs are an error until `output_crs=` is given.
- Any resampling or reprojection is recorded as a note on the data_ref.

`output_crs=` is deliberately not `crs=`: `crs=` only ever assigns a missing CRS (D-013), and the
two must not be confused.

**Alternatives.** A separate `stac_load` tool: more surface for the same operation. odc-stac's own
defaults: convenient, and silent.

## D-026 Scale and offset come from the catalogue, and contradictions are flagged (M3, 2026-10-23)

**Context.** Sentinel-2 L2A files declare no scaling. The physical scaling exists only in each
item's `raster:bands`, and it changes between processing baselines. Testing over Sydney found
items where the metadata contradicts the pixels. Earth Search marks them
`earthsearch:boa_offset_applied = true` yet they still declare offset −0.1. Clear harbour water
stores red values of 15–98, which that offset turns into negative reflectance; without it the
values are 0.002–0.01, plausible for dark water.

**Choice.** Scale and offset are attached to bands (`scale_factor`, `add_offset`) only when every
loaded item agrees; otherwise bands stay raw, with a warning. loci keeps the metadata as declared
and does not "fix" it. When an item says its offset was applied but still declares one, the load
warns that the offset may be counted twice. `value_range` names the conversion it applied, so an
out-of-range reflectance points at the scaling.

**Alternatives.** Dropping the offset when `boa_offset_applied` is true: probably right for these
items, but a provider-specific guess. It is left as an open question for the user.

## D-027 Statistics and previews of STAC stacks come from coarser re-reads (M3, 2026-10-23)

**Context.** Striding through a lazy stack still reads every full-resolution chunk. For a year of
10 m scenes that is most of the data.

**Choice.** When a STAC stack exceeds `max_pixels`, or is larger than a preview, it is loaded again
at `resolution × factor`. GDAL serves those reads from the COGs' overviews. The summary labels the
result `overview` with its decimation, as for files with stored overviews. Measured over Sydney:
`inspect` of a 41-step, 4-band, 10 m stack (1408 × 1134 px) took 25 s at 1/5 resolution, and a
true-colour preview took 3.5 s.

**Alternatives.** Statistics on a subset of time steps: faster, but they would miss the cloudy or
broken scenes the checks are meant to find.

## D-028 preview: small PNGs with stated choices (M3, 2026-10-23)

**Context.** A vision-capable model should be able to look at what it is working on. Every choice
behind a picture (bands, time, stretch) affects what it sees.

**Choice.** `preview` writes a PNG to `.loci/previews/` at most `width` pixels wide (default 512)
and reads pixels at about that resolution.
- **Bands:** true colour when red, green and blue bands exist; otherwise one band in viridis.
- **Stretch:** one shared 2–98% stretch on physical values. Nodata is transparent.
- **Time:** the least cloudy step of a STAC load by default (from item metadata), or the step
  nearest `time=`.
- **Vectors:** drawn to scale on white, optionally coloured by a column, with geographic layers
  corrected for latitude.

The result says what the picture shows and why that step was chosen. Matplotlib is used without
pyplot, so there is no global state.

**Alternatives.** Per-band stretches for RGB: punchier pictures but false colour balance. The first
time step by default: often cloudy, and so useless.

## D-029 Real-data tests over Sydney (M3, 2026-10-23)

**Context.** The user asked for M3 to be tested over Sydney, Australia rather than Sicily.

**Choice.** All network tests now use Sentinel-2 tile 56HLH (EPSG:32756) over Sydney Harbour:
- a clear 18 June 2024 scene for loading, inspecting and grid checks;
- a 2024 search;
- a June stack for load, preview and validate.

Assertions stick to stable facts (CRS, tile, grid, files written), so catalogue fixes upstream do
not break them. Two more harmless third-party warnings are filtered by exact message, under D-017:
odc-geo's use of a deprecated shapely function, and odc-loader's overview reads, which warp from a
dataset opened without its transform. Tests confirm the values those reads return.

**Alternatives.** Keeping both places: twice the network time for no extra coverage.

## D-030 drop_offset: an explicit, recorded way past contradictory offsets (M3, 2026-10-23)

**Context.** D-026 left open what to do about Earth Search scenes whose stored values already have
the offset applied but still declare one. The user agreed with an explicit option over loci
deciding on its own.

**Choice.** `load(<search data_ref>, bands=[...], drop_offset=True)` leaves the items' declared
offsets out, so physical value = stored × scale.
- The choice is kept in the data_ref's read options, which makes it part of the record, and later
  of lineage.
- A note names the offset that was left out.
- Items not marked `earthsearch:boa_offset_applied = true` raise a warning, because their stored
  values may still need the offset. Over Sydney in 2024 that flags 1 of 42 scenes.
- Without the option, the "counted twice" warning now points to it.

A network test checks that over Sydney the declared offset gives negative red reflectance and
`drop_offset` gives none. Unlike the other network tests, it deliberately tracks this upstream
quirk, so a fix in Earth Search shows up as a failure.

**Alternatives.** Dropping the offset automatically whenever the flag is set: likely right today,
but a silent, provider-specific guess. A per-band option: finer, but nothing needs it yet.

## D-031 uv 0.13.5 (2026-10-23)

**Context.** D-005 pinned CI to uv 0.11.32, the version this environment happened to have, and
noted that bumping is a one-line change. uv 0.13.5 is current, and the user asked for the bump.

**Choice.** CI pins uv 0.13.5 and the `uv_build` backend is bounded to `>=0.13.5,<0.14.0`.
Re-resolving `uv.lock` with uv 0.13.5 gives an identical file, so no package versions change: this
updates the tool, not the dependencies.

**Alternatives.** Leaving CI unpinned: always current, but upstream releases could break CI with no
change here (D-005).

## D-032 Provenance: a ledger per data_ref, and sidecars that travel with files (M4, 2026-10-23)

**Context.** The brief asks for a JSON record beside every output: inputs, parameters, the call,
a code hash, package versions, timestamps and the validation report. Some outputs are data_refs
held in the workspace; others are plain files a scientist will copy elsewhere.

**Choice.** One `Provenance` model in `models.py`, written in two places.
- Every data_ref gets `.loci/provenance/<data_ref>.json`, from `load`, `stac_search` and `run`.
- Files written by `run` also get `<file>.prov.json` beside them, so the record goes where the file
  goes.
- Inputs are `SourceInput`s: a parent data_ref, an outside URI, or both, plus the STAC item IDs
  behind them.
- The call is the tool name and its non-empty arguments.
- `software` records Python, loci's version, a hash of loci's own source, GDAL and the versions of
  the core geo packages, so "which code made this" survives editable installs.
- `validate` writes its latest report into the record of every data_ref it covered, with a
  timestamp.

Records are written atomically. Refs from before M4 have no record, and `lineage` falls back to
their workspace record and says so.

**Alternatives.**
- Sidecars only: data_refs have no file of their own to sit beside.
- One append-only log file: easy to write, but it has to be parsed and filtered to answer "how was
  this made".
- A full provenance standard such as W3C PROV: more expressive, but heavier than the questions
  lineage needs to answer. The model can be exported to it later.

## D-033 run: a script in its own process and folder, with declared inputs (M4, 2026-10-23)

**Context.** Analyses beyond the built-in tools need arbitrary code, but every output still has to
say exactly what it was made from, and large data must stay out of the model's context.

**Choice.** `run(code=... | path=..., inputs={name: data_ref}, parameters={...}, timeout=...)`.
- The script runs with the project's Python in a subprocess, inside `.loci/runs/<run_id>/`. That
  folder keeps the exact script, its inputs and parameters, stdout, stderr and `run.json`.
- Scripts reach data through `loci.script`: `read(name)` opens only declared inputs, and `save()`
  writes to `outputs/`.
- After a successful run, every file in `outputs/` is registered. Spatial files become data_refs;
  other files are kept as files. Each gets provenance naming the run, the script's hash, its inputs
  and its parameters, and is validated straight away. The result lists failures.
- Failed and timed-out runs register nothing, but keep their folder for debugging.
- Arguments, refs and parameters are checked before anything runs.
- The result carries only the tails of stdout and stderr.

`run` is not a sandbox. A script can still open files directly. Declared inputs make the honest
path the easy one; they do not enforce it.

**Alternatives.**
- `exec` in the server process: faster, but a crash or a leak takes the server down.
- Containers: real isolation, but heavy for a single-user tool. They can come later behind the
  same signature.
- Scanning the script for file reads: unreliable, and it would give false confidence.

## D-034 lineage: a walk over provenance records (M4, 2026-10-23)

**Context.** "Where did this number come from?" should have a short, complete answer.

**Choice.** `lineage(target)` takes a data_ref, or a path to a file with a sidecar, and walks parent
data_refs breadth-first.
- Each step names what produced it, gives a one-line description, and lists its parents, its
  outside sources, its STAC item count and its latest validation status.
- Load steps name the options the caller set (for example `drop_offset=True` or an assigned
  `crs=`), because they change the data.
- `original_sources` gathers every outside URI. STAC item IDs are gathered across steps; the first
  10 are listed alongside the total.
- Changed sources and ancestors missing from the workspace are warnings. Records from before M4
  are notes.
- The target's own record is included without its validation report, so the result stays small:
  2.6 KB for the Sydney NDVI run.

**Alternatives.** Returning every record in full: complete, but it grows with depth and mostly
repeats itself. A graph format (e.g. DOT): useful for a future UI, and it can be built from the
same steps.

## D-035 Float outputs carry physical values, never inherited scaling (M4, 2026-10-23)

**Context.** The first Sydney NDVI run validated and looked fine, but the preview stretch was
-9e-05 to 9e-05. xarray keeps attributes through arithmetic, so NDVI inherited red's
`scale_factor` of 0.0001. `save()` wrote it into the GeoTIFF, and every reader then scaled NDVI by
10,000. Validation passed because NDVI in [-1e-4, 1e-4] lies inside [-1, 1].

**Choice.**
- `script.physical(da)` turns raw bands into floats: nodata becomes NaN, scale and offset are
  applied, and the packing attributes are dropped. The docs and examples compute on its output.
- `save()` keeps scale and offset only for integer bands, where they are packing. For float bands
  it writes 1 and 0. If a float band carries other values, it records a warning through
  `script.warn()`, and `run` returns that warning in its result.
- A test runs the same NDVI with and without `physical()`. With it, the result is the known answer;
  without it, the result is wrong (the offset is ignored) and the run is flagged.

Over Sydney, the same script on raw bands also shows why `physical()` is the default path. The
bands are uint16, so `nir - red` wraps around over water, and "NDVI" reaches 21,845. `run`
validates its outputs, so it fails that one and says why.

**Alternatives.**
- Clearing attributes globally with `xr.set_options(keep_attrs=False)`: it hides the symptom for
  scripts loci writes, but not for scripts users write.
- Writing inherited scaling and trusting the script: that is how this bug happened.

## D-036 script.read gives physical values by default (M4 follow-up, 2026-10-23)

**Context.** D-035 made `script.physical()` the way to get physical values, but scripts had to
remember to call it. Every failure seen over Sydney came from forgetting it, and the agent writes
most scripts. The user agreed to make it the default.

**Choice.** `script.read(name)` applies `physical()` to raster bands that hold measurements:
- bands that declare a scale or offset;
- float bands with a nodata value, so nodata becomes NaN and drops out of means and medians.

Integer bands without scaling stay as stored, because converting them would break them: class
maps (such as Sentinel-2's scene classification) and bit-packed quality flags. `read(name,
raw=True)` returns every band as stored, and `physical()` remains for bands read that way. It is
idempotent, so calling it on values that are already physical does no harm.

Uint16 integers become float32, which doubles memory. Reads stay lazy, so this matters only for
what a script computes.

**Alternatives.** Keeping it explicit (D-035): faithful to storage, but easy to forget. Converting
every band: simpler to state, but it breaks class maps and bit flags.

## D-037 The MCP server adapts loci.tools without restating them (M5, 2026-10-23)

**Context.** The tool functions and `models.py` are the stable interface. The MCP server must
expose them to Claude Code without becoming a second copy that drifts.

**Choice.** `server.py` builds the MCP tools from the functions in `loci.tools`. Each one is the
same function with the Workspace filled in, so its arguments, docstring and result schema are
exactly those of the Python tool. A test checks that every tool's MCP arguments match its Python
signature.
- Results go out as structured content plus compact JSON text with empty fields left out.
- `preview` also returns its PNG, so the agent can look at the picture (a few hundred tokens).
- A `LociError` becomes a tool error with its hint. Any other exception comes back as its type and
  message, instead of the SDK's bare "Error executing tool".
- Calls run one at a time, because the SDK runs sync tools in threads and the workspace cache is
  shared.
- The server's instructions give the agent the working rules: keep data in data_refs, pass
  assumptions on, validate before concluding, and read inputs only through `loci.script`.
- `.mcp.json` sets the server's tool timeout to 61 minutes, just over `run`'s own limit. In a real
  session, Claude Code abandoned an 85 s `run` after the 60 s that `MCP_TOOL_TIMEOUT` allowed in
  that environment. The run then finished unseen while the agent retried.
- `.mcp.json` starts it with `uv run --quiet loci-mcp`. Without `LOCI_WORKSPACE`, the workspace is
  `.loci/` in `$CLAUDE_PROJECT_DIR`, which Claude Code sets for MCP servers and hooks, so both
  share one workspace whatever their working directory.
- `.claude/settings.json` lists `loci` in `enabledMcpjsonServers`. Claude Code ignores that in a
  folder the user has not trusted, so the trust prompt still protects new clones.

Built on the MCP Python SDK 2.3, where `FastMCP` is now `MCPServer`.

**Alternatives.** Hand-written wrappers per tool: easy to read, but every signature written twice.
Returning only text: simpler, but it loses the output schema a future UI can use.

## D-038 Automatic validation as a PostToolUse hook with report, block and off (M5, 2026-10-23)

**Context.** The brief asks for hooks that run validation automatically, so that checking does not
depend on the agent remembering. The user asked for it to be switchable.

**Choice.** `.claude/settings.json` runs `loci-hook` after `mcp__loci__load`, `stac_search` and
`run`. The hook finds the data_refs the tool made and validates each one. Run outputs are not
validated twice: `run` has already validated them, and the hook reuses that report.
`LOCI_VALIDATION_HOOK` sets the mode, through the `env` block in the same settings file:
- `report` (default): the result goes next to the tool's output as `additionalContext`, and Claude
  carries on.
- `block`: a failure goes back as `decision: "block"` with a reason. Claude is asked to fix the
  input or the call, or to tell the user why the result is still usable.
- `off`: nothing runs.

A failure is also shown to the user as a `systemMessage`. Personal overrides go in
`.claude/settings.local.json`, which git ignores.

Facts checked against the current hooks documentation and a real session:
- Claude Code passes an MCP result to the hook as its JSON text.
- The `env` from settings reaches hook commands.
- A PostToolUse "block" cannot undo the call. It only puts the reason in front of Claude.

The hook never breaks a session: if it fails, it says so in a `systemMessage`. It has a timeout of
300 s, because validating a STAC stack reads pixels.

**Alternatives.**
- Validating inside each tool: simpler, but it couldn't be switched per user, and the brief asks
  for hooks.
- A PreToolUse hook that refuses later calls on failed data_refs: stricter, but it needs a way to
  override deliberate use, such as computing a mean that needs no CRS.

## D-039 One harmless rasterio warning silenced at runtime (M5, 2026-10-23)

**Context.** In the first end-to-end session, odc-loader's overview reads printed rasterio's "no
geotransform" warning into the stderr of scripts and the hook. The agent reported it as something
it had not investigated. The tests already filter it (D-029), and the values are correct.

**Choice.** `loci.io.stac` ignores exactly that message, and only when rasterio's warp module
issues it. A file that really has no geotransform still warns when it is opened, and loci's
`crs_defined` check reports it anyway. A test checks both sides of the filter.

**Alternatives.** Leaving it in: honest, but noise that costs the agent's and the user's
attention. Filtering through `PYTHONWARNINGS` in `run`: it covers scripts but not the hook.

## D-040 End to end in real Claude Code sessions (M5, 2026-10-23)

**Context.** Unit tests cover the server through an MCP client, over stdio too. They cannot show
that Claude Code starts the server from `.mcp.json`, that the hook's output reaches Claude, or how
an agent actually uses the tools.

**Choice.** `uv run pytest -m claude` runs short `claude -p` sessions in the project, with only
the loci tools allowed. It loads a raster with no CRS in report and block modes, and asserts on
the hook's output in the event stream rather than on the model's wording. These tests cost model
usage, so they are deselected by default and do not run in CI.

A real question over Sydney ("how green was the land in June 2024?") was also run by hand twice.
- Claude searched, then loaded red, NIR and SCL with an explicit resolution: 20 m in one run,
  10 m in the other.
- It noticed the offset warning, checked the values (with `inspect` in one run, `validate` in
  the other), and reloaded with `drop_offset=True`.
- It masked clouds with SCL in a `run` script, and reported a land median NDVI of 0.46. Both runs
  agreed, and the answer came with caveats on cloud, coverage and resolution.
- The first session also found a bug: an integer count band inherited a NaN nodata and
  `script.save` crashed. Such a band is now written without nodata, with a warning.

**Alternatives.** Scripting the MCP calls without a model: deterministic, but it does not test
what matters, which is how an agent uses the tools and the hook in Claude Code.

## D-041 The benchmark: five traps with exact answers, scored two ways (M6, 2026-10-23)

**Context.** The brief asks for five benchmark tasks and `benchmarks/run.py`. The guiding question
is whether a scientist gets a right answer they can trust. Real data has no exact answers, and
judging free text is noisy.

**Choice.** Five realistic questions on synthetic data from `loci.synthetic`. Each has one trap
that makes a careless answer measurably wrong:

| Task | Data | Trap | Answer |
|---|---|---|---|
| cloudy-ndvi | STAC time series | cloud, marked only in the 20 m SCL band | 0.5477 |
| catchment-elevation | DEM in UTM, polygons in WGS 84 | CRS mismatch, nodata | 50 m |
| forest-area | polygons in WGS 84 | area in square degrees or Web Mercator | 2.00 ha |
| ndvi-at-sites | CSV without a CRS, NDVI raster | CRS not stated, 2 of 10 sites off the map | 0.300 |
| steep-terrain | DEM in degrees, heights in metres | slope with mixed units | 60% |

There are two ways to answer them.
- **Reference mode** answers each task through loci's tools, as a careful agent would. It also
  records what loci reported about the trap. These runs are in the test suite, so every commit
  checks the engine's answers. Each answer is itself a `run` output, so `lineage` traces it.
- **Agent mode** (`--agent`) asks Claude Code each question in a fresh session with only the loci
  tools. It scores:
  - the answer against the tolerance (the main score);
  - whether the reply mentions the trap, by regular expression (rough);
  - turns, cost, time, tool calls and automatic validations.

  Each attempt keeps its data, workspace and transcript.

Task 1 starts from a STAC search made by the real `stac_search`, with the catalogue simulated by
synthetic scenes; the patch lives only in the benchmark. Agent runs cost model usage and vary from
run to run, so they are not in CI; `--repeat` measures the spread.

**Baseline (2026-10-23).**
- Reference mode scored 5/5.
- Agent mode, one attempt per task, also scored 5/5, and every reply named its trap. Each task
  took 4 to 6 turns, 17 to 31 s and $0.08 to $0.11 ($0.49 in all).

Three things stand out:
- The tasks are too easy to tell one setup from another.
- No session called `validate`, `inspect`, `preview` or `lineage`; they used only `load` and
  `run`. The hook validates each new data_ref on its own, so the checks that compare inputs (CRS
  consistency, overlap) never ran. The agent's own knowledge caught those traps.
- In forest-area the agent called UTM "more accurate" than an equal-area projection. Here it is
  the other way round, because UTM's scale factor shrinks areas by 0.08%. The answer was still
  within tolerance, so the score missed a wrong reason.

**Alternatives.**
- Real data with judged answers: realistic, but it cannot be scored exactly.
- Scripted answers only: deterministic, but they say nothing about the agent.
- A model as judge of the caveats: richer, but it adds a second noisy model to the score.

## D-042 No terrain library yet (M6, 2026-10-23)

**Context.** The brief asks for one terrain library to be chosen and recorded. The user agreed:
xarray-spatial if slope and aspect are all that is needed, pysheds for catchments and flow
routing. The benchmark needs only slope.

**Choice.** No terrain library in M6. What was checked in October 2026:
- xarray-spatial 0.10.19 requires `numpy<2.5`, so it would downgrade the project's numpy 2.5.4.
- pysheds 0.5 resolves with the current stack. It brings numba, scikit-image and scipy, but it is
  built for hydrology, not slope.
- richdem has not been released since 2018.

Slope on a metric grid takes a few lines of numpy, which every `run` script has. The reference
solution reprojects to UTM and uses `np.gradient`. The choice goes back to the user: pysheds when
a task needs flow routing, xarray-spatial once it supports numpy 2.5.

**Alternatives.** Downgrading numpy for xarray-spatial: it changes the core numeric library for
one function. Adding pysheds now: a heavy dependency that nothing yet uses.

## D-043 The map interface is our own local web app (Phase 2 MVP, 2026-10-23)

**Context.** Phase 2 is a map-first IDE in the style of Cursor: an agent the scientist talks to,
a map to zoom and explore, the code it runs, and a choice of model. The user asked for a slick,
modern, minimal design and agreed to the dependencies below.

**Choice.** `loci-app` serves a React web app and an HTTP API over the same workspace and tools.
- **API in `loci.web`.** It uses Starlette and uvicorn, which mcp already brings. It reads the
  workspace and calls the tools, and never changes the engine: `tools/` and `models.py` are
  unchanged. New layers reach the browser through server-sent events, driven by a fingerprint of
  the workspace, so layers made by the agent, a run or Claude Code all appear.
- **Drawing.** Rasters from files are served as map tiles by rio-tiler, in physical units
  (`unscale=True`) and stretched to the 2nd–98th percentiles of its preview. NaN pixels are
  transparent even when the file declares no nodata. STAC stacks and rasters with a time
  dimension are drawn as one georeferenced preview picture, and the app says so. Vectors are
  sent as GeoJSON in WGS84. Reprojection for display happens only on the server and changes no
  data_ref.
- **Basemap.** OpenFreeMap's dark style: OpenStreetMap data, no API key. Labels sit above the
  data layers. CARTO's free tiles now need a key.
- **Agent.** The Claude Agent SDK runs Claude Code with only loci's MCP server (`tools=[]`,
  `strict_mcp_config`, `setting_sources=[]`). The validation hook runs in process, so each check
  reaches the chat as its own event. The model (Opus, Sonnet or Haiku) is chosen per message.
  Each message carries the map's layers and the user's selection, because no tool lists the
  workspace.
- **Code.** Scripts live as plain `.py` files under the project's `analysis/` folder, edited in
  Monaco and executed with `run`. The app finds the names a script reads with `script.read("…")`
  and asks for a layer for each, so every run declares its inputs.
- **Frontend.** React, TypeScript, Vite, MapLibre GL and Monaco, built into `app/dist`. Versions
  are pinned exactly.

**Alternatives.**
- A Jupyter or QGIS plugin: familiar tools, but they cannot give the agent-first layout or the
  design.
- A VS Code extension: brings the editor, but the map would be a webview inside an IDE built for
  code.
- A desktop app (Tauri or Electron): could come later around the same web app.
- A tile server such as TiTiler: it brings more than an MVP needs. rio-tiler is the library
  inside it.
- Mapbox GL or Google Maps: they need keys. MapLibre is the open fork.

## D-044 `run` checks its declared inputs against each other (Phase 2, 2026-10-23)

**Context.** In the benchmark baseline (D-041), no agent called `validate` on the layers it
combined. The hook checks each new data_ref on its own. So the checks that compare inputs (same
CRS, overlapping extents, aligned grids) never ran, and only the agent's own knowledge caught
those traps.

**Choice.** When a script declares two or more inputs, `run` runs those comparison checks before
it executes. The report is `RunResult.input_checks`, a `ValidationReport`, plus one warning when
it is not a pass. This adds a field to `models.py` and a paragraph to the `run` docstring.
- The checks read no pixels, so they are cheap even for remote STAC stacks. Each input's own
  checks are left to the hook.
- The report is kept in the run's `run.json`, not on the inputs' provenance records. A layer's
  badge stays about that layer, not about one way of combining it.
- The run goes ahead whatever the checks find. Often the script itself is where the mismatch is
  resolved, for example by reprojecting one input explicitly. The report tells the agent and the
  scientist to say how.

**Alternatives.**
- Stop the run on a fail: that blocks the scripts that fix the mismatch.
- Ask the agent to call `validate` first: the baseline shows it does not.
- Full `validate` on the inputs: it re-reads pixel statistics that the hook already checked.

## D-045 Exploring over time and place on the map (Phase 2, 2026-10-23)

**Context.** The MVP drew STAC stacks as one preview picture, so a scientist could not zoom into
them or step through their dates. Areas of interest had to be typed as coordinates. Checks were
badges, with no way to see where on the map a problem lay. The user agreed this milestone should
make the map work for "how did this place change?".

**Choice.**
- **Every raster is tiled.** GeoTIFFs are still read by rio-tiler straight from the file. Other
  rasters (STAC loads, time stacks, files with an assigned CRS) are tiled with rio-tiler's
  `XarrayReader` from one time step held in memory as physical values. That step is decimated
  if it is over 16 million pixels per band, and the legend says so. The latest eight steps are
  cached, so panning reads the data once. The preview-picture mode is gone.
- **One stretch for every date.** The stretch comes from the step shown first (the least cloudy
  by default) and stays in the tile URL as the date changes. One colour then means one value on
  every date. Per-date stretches would make change invisible.
- **Values through time are flagged, not cleaned.**
  - Each date at a clicked point carries the scene's cloud cover (item metadata) and, when an
    `scl` band was loaded, the Sentinel-2 scene class there.
  - Dates where the class hides the ground are drawn hollow and left out of the line. Their
    values stay in the chart and its table; nothing is dropped.
  - Packed values are rounded to their scale's precision, so 0.0001 × 1000 − 0.1 reads 0.
- **Checks on the map.**
  - A failed `value_range` check can show only its out-of-range pixels, in red, as a tile
    overlay (`outside=low,high`).
  - Checks that compare inputs can outline each input's footprint.
  - This is display only; nothing is written to the workspace.
- **Areas and dates are context for the agent.** A box drawn on the map goes to the agent as a
  WGS 84 bbox with every message. So do today's date and the date shown for the selected layer.
  No new tool is needed: `stac_search` already takes a bbox.

**Alternatives.**
- Write each step to a temporary COG and tile it from the file: overviews and less memory, but
  a second copy of the data and its provenance to explain.
- Per-date stretches: brighter pictures, but they hide change.
- Mask cloudy dates out of the chart: tidier, but it is a silent drop.
- A drawing library (mapbox-gl-draw): polygons and editing, but a dependency for one box.

## D-046 Offsets already applied are left out by default (Phase 2, 2026-10-29)

**Context.** D-030 left it to the caller to pass `drop_offset=True` for Earth Search scenes whose
stored values already include the offset they still declare. In use, agents loaded without it.
- Both agent sessions over Sydney got reflectance about 0.1 too low.
- The map showed impossible negative red values.
- Each time, the agent's only response was a warning.

A wrong default that is merely flagged is still a wrong number in front of a scientist.

**Choice.** `load`'s `drop_offset` now defaults to an automatic choice, made from the items'
metadata, for bands that declare an offset:
- Left out when every item is marked `earthsearch:boa_offset_applied = true`. A note says so and
  names `drop_offset=False` as the way back. Lineage says the offset was left out.
- Applied when no item is marked, as before.
- An error when only some are. No one value suits them all, so the caller must choose. This
  follows the rule for mixed resolutions without `resolution=`.

`True` and `False` still decide outright. `ReadOptions.drop_offset` accepts `"auto"` in a
request, but a record always stores the decision, so a data_ref reopens identically whatever the
items say later. Records made before this change hold `None`, which still means "apply", so
their values do not change. The Sydney network test now checks both sides of the quirk: the
default gives no negative reflectance, and `drop_offset=False` does.

**Alternatives.**
- Keep the explicit option only (D-030): it stays wrong by default for nearly every recent
  Earth Search scene.
- Leave the offset out per item, by its own mark: it could mix two meanings of a value in one
  band.
- Decide again each time a data_ref is opened: a data_ref's values could change after the fact.

## D-047 The agent's analyses are scripts you review (Phase 2, 2026-10-29)

**Context.** The concept for the map interface was a Cursor-style loop: the agent writes code,
the scientist reads it and decides on changes. The MVP agent ran inline code, kept only in run
folders, so its work was hard to see and could not be edited and re-run. The user asked for the
loop. They agreed that new scripts could run on the agent's own say-so, with changes to existing
ones waiting for them, and that "review every run" should be available too.

**Choice.** In the app, the agent works on files in the project's `analysis/` folder.
- **Files only.** Its tools are the loci MCP server plus Claude Code's Read, Write and Edit. A
  PreToolUse hook limits those three to `.py` files under `analysis/`, whatever Claude Code's own
  rules would allow, so data still reaches it only through loci's tools. `run` must be given a
  script by `path=` under `analysis/`; `code=` is refused with a message saying so.
- **Every call is checked.** Nothing is allowed up front: each call reaches a `can_use_tool`
  callback.
  - Writing a new script goes ahead.
  - A Write or Edit that would change an existing script pauses the agent. The app gets a
    `proposal` event with the text before and after, and shows it as an inline diff. The
    scientist's Accept or Reject comes back through `/api/agent/decide`.
  - A rejection reaches the agent as the tool's error, with the scientist's note.
  - With review set to "every run", runs pause the same way, showing the script and its inputs.
  - The query runs beside the event stream, so a review reaches the app while the agent waits.
    A turn the browser abandons cancels what it was waiting on.
- **Results link to their code.**
  - `run` now records a script by its absolute path.
  - A layer's detail links to the script under `analysis/` and says when the file has changed
    since that run.
  - `/api/runs/{id}/script` shows the exact version that ran.
- **Live files.** The event stream says when scripts change, so the editor reloads a script the
  agent saved, or warns if there are unsaved edits.
- **Follow-ups.** The agent ends answers with a "Next:" list, which the chat shows as one-click
  questions.

Claude Code in a terminal is unchanged: it still runs `code=` scripts, and its own permission
settings govern edits.

**Alternatives.**
- Save inline code to `analysis/` automatically: files appear, but the agent does not know their
  names and cannot edit them.
- Review only after the fact: simpler, but a change that runs before anyone reads it is what
  the scientist asked not to have.
- A custom write-script tool on the MCP server: it duplicates Claude Code's well-tested editing
  tools, and the rule is to add loci tools only when a benchmark needs them.

## D-048 Other providers' models run in loci's own loop (Phase 2, 2026-10-29)

**Context.** The user asked to use models from other providers. The agent ran only in Claude
Code, through the Claude Agent SDK, which runs only Claude models: directly, or through Amazon
Bedrock, Google Vertex AI or Microsoft Foundry. The user chose to keep that and add a second
setup for everything else. OpenAI, Google Gemini and OpenRouter come first, built on Pydantic AI.

**Choice.**
- **Two engines, one contract.** Claude models still run in Claude Code. Other models run in a
  Pydantic AI loop (`web/loop.py`) with:
  - the same instructions;
  - the loci MCP server's tools, through an MCP client, with the server started as a subprocess
    as Claude Code starts it;
  - its own Read, Write and Edit for scripts under `analysis/`.

  Every call goes through the same permission step, so reviews, the refusal of `code=` and the
  validation of new data_refs behave as they do in Claude Code. The app gets the same events.
  What both engines share is in `web/turn.py`.
- **Models are named `<provider>:<model>`**, such as `claude:opus` or `openai:gpt-6-astra`. A
  bare name is an error, not a guess. `/api/models` lists every provider, whether it is set up,
  and how to set it up.
- **Keys and choices belong to the user, not the project.** They come from environment variables,
  read at start, or from `~/.config/loci/config.toml` or `$LOCI_CONFIG`, read for every message.
  The environment wins. The file can also list each provider's models and choose Claude's route
  (`anthropic`, `bedrock`, `vertex` or `foundry`), with whatever environment that route needs.
  Keys never reach the browser.
- **Conversations.** The loop keeps each conversation's messages in `.loci/chats/<id>.json`. A
  failed answer is not added. Any of the loop's providers can carry on a loop conversation.
  Claude Code and the loop cannot carry on each other's, so switching between them starts a new
  conversation, and the app says so before the next message.
- **Cost and errors.** Pydantic AI prices an answer with genai-prices where it can; otherwise the
  app shows tokens. Errors carry their hint separately. A provider's refusal, such as a rejected
  key or an unknown model, comes back in plain words with what to do.

**Testing.**
- Scripted models with the MCP server in process.
- Each provider's own client against a mock of its HTTP API.
- A real OpenRouter request with a placeholder key, which came back as a clear error.
- No real answer from OpenAI, Gemini or OpenRouter has been checked yet, for want of keys.

**Alternatives.**
- One loop for every provider, Claude included: one code path, but Claude would lose Claude
  Code's tested editing tools, its context management and sign-in with a Claude subscription.
- A gateway such as LiteLLM behind Claude Code: the SDK does not support it, and tool use would
  depend on translation between formats.
- Our own client for each provider: more code to keep current. Pydantic AI already maps tools,
  images, usage and errors for each provider.
- Keys in a project `.env`: they end up in repositories.

## D-049 loci installs with one command on Windows, macOS and Linux (Phase 2, 2026-10-29)

**Context.** The user wants to demo loci on their own data, on Windows, from something they can
install. loci ran only from a clone, with Node to build the app and several commands. Anthropic
does not allow apps built on its Agent SDK to sign people in with a Claude subscription; they take
an API key, or Bedrock, Vertex or Foundry. Once loci runs on other people's computers, a local app
that runs code needs protecting from web pages that send it requests.

**Choice.**
- **A wheel with the web app inside.** `npm run build` bundles the app into
  `src/loci/web/static`, which the package serves, so a release wheel needs no Node. The Release
  workflow builds it and pins every dependency to the versions CI tested (`constraints.txt`, from
  uv.lock). It then installs the wheel on Windows, macOS and Linux and uses it as a person would
  (`scripts/smoke_install.py`). A `v*` tag publishes it as a GitHub release.
- **One command.** `install.ps1` and `install.sh` install uv if needed, then the latest release
  with `uv tool install`, which brings Python 3.12 and every package into a folder of their own.
- **The `loci` command.** `loci open FOLDER` serves the app on a folder (workspace `.loci/`,
  scripts `analysis/`) and opens a browser. `loci demo` writes a project of synthetic data with
  exactly known answers and the benchmarks' traps, and offers its questions in the app.
  `loci doctor` checks the installation.
- **Keys pasted into the app.** They are saved to `keys.toml` in the user's settings folder
  (`%APPDATA%\loci` on Windows), readable by the user only, and never sent back to the browser.
  The user's own `config.toml` beside it is only ever read. Claude counts as set up with an
  Anthropic key, a gateway the user has set up (`ANTHROPIC_BASE_URL`), or a cloud route. A
  banner and the model menu say how to set it up.
- **Only this computer's own pages.** The app refuses requests for any other host name, which
  stops DNS rebinding, and refuses changes asked by a page from another origin.
- **Windows.** CI runs on Windows and macOS. Text is read and written as UTF-8 everywhere; a test
  checks the source for this. Paths sent to the app use `/`.

**Alternatives.**
- PyPI: the name is taken, and a release on GitHub is enough for now.
- Commit the built app: large generated files in every diff.
- A desktop bundle (an installer with its own Python): heavier to build for three systems, for
  the same result as uv.
- Sign people in with their Claude subscription: Anthropic does not allow it for SDK apps.
- Edit `config.toml` in place: it needs a TOML writer that keeps comments, and the user's own
  file would change under them.

## D-050 Data in and out of the app, beside the scientist's own files (Phase 2, 2026-10-29)

**Context.** To demo loci on their own data, the user needs to put files on the map and take
results away. Before this, data came in only through the agent (`load` by path) or STAC, and
results stayed in run folders inside the workspace.

**Choice.**
- **In.** The layers panel lists every file loci can read in the project folder, and a click
  puts one on the map. Hidden folders, the workspace and `analysis/` are skipped, at most 500
  files and six folders deep.
  - Files dropped on the map, or picked with "Add files", are saved in `data/` and put on the map.
    A shapefile's companion files come with it.
  - A file already there is replaced only after the app asks, and one dropped again unchanged is
    left as it was, so its layer still matches it.
  - Files added in the app are validated at once, as the hook does for the agent's loads.
- **Out.** A layer is exported as it is held: GeoTIFF for rasters, one time step at a time for a
  time stack; GeoPackage, GeoJSON or CSV for vectors. Nothing is reprojected or resampled.
  - Exports go to `exports/` beside the data, never replacing an earlier one, and the app offers
    them as a download.
  - Each export gets a provenance sidecar (`produced_by: export`), so `lineage` traces the file
    back to its sources.
  - Where a format cannot hold the data as they are, the export says so: GeoJSON in a CRS other
    than WGS 84, the CRS a CSV cannot record, bands given one data type.
  - `run` outputs and exports share one GeoTIFF writer (`io/write.py`).
- `export` is a tool function but not an MCP tool: agents save what they make with
  `loci.script.save` in a run.

**Alternatives.**
- Upload into the workspace: the files would be hidden in `.loci/`, away from the scientist.
- A file browser over the whole disk: more to secure, and a project folder is enough;
  `loci open` takes any folder.
- Export as COG or NetCDF now: GeoTIFF serves every GIS; NetCDF comes with gridded data (P2.4c).

## D-051 Gridded data: NetCDF and Zarr, read where they are needed (Phase 2, 2026-10-29)

**Context.** The user wants to demo loci on gridded climate data: reanalyses such as ERA5 and
climate model output, as NetCDF downloads and as Zarr stores in the cloud. These grids differ from
GeoTIFFs. They name their axes by CF conventions, not x and y, and often state no CRS. They have
more dimensions (pressure level, ensemble member) and hold values in kelvin. Their longitudes may
run 0 to 360. Some, like the public ERA5 archive, are enormous: 273 variables, hourly from 1900,
each hour one global chunk. The user approved netCDF4 and zarr as dependencies; reading Zarr over
HTTPS also needs aiohttp, which fsspec (already installed) uses as its HTTP client.

**Choice.**
- **One raster form.** `.nc` files and `.zarr` stores (local, or https://) load as rasters with
  one variable per band and dims (time, y, x), values as stored, as GeoTIFFs and STAC loads do.
  - CF attributes, units or names say which dimensions are longitude, latitude and time. The
    renaming is noted.
  - Bands now carry their `units` and `long_name`.
- **Flagged, not guessed.**
  - The CRS comes from the variables' grid_mapping. Without one, a longitude/latitude grid is
    taken to be WGS 84 with a warning, as for a table of longitudes and latitudes.
  - Variables must share their dimensions.
  - A dimension with several values must be chosen with the new `select=` option of `load`. One
    with a single value is taken, with a note.
  - Calendars other than the standard one are refused with a hint.
  - Longitudes 0 to 360 are kept, with a note; the map shows them from -180.
- **Read only what is needed.**
  - Stores open without dask. The variables, period, level and area chosen (`bands=`, `select=`,
    `bbox=`) are cut out before the remainder is chunked as it is stored, so opening ERA5 takes
    seconds and a few hundred MB.
  - A time axis longer than 10,000 steps must be narrowed.
  - Statistics sample at most 24 evenly spaced time steps, and say so.
  - A point's values through time are refused, with an estimate of the read, when the store's
    chunks would make it large: over 256 MB from the cloud, 4 GB from disk.
  - A time stack's colour scale is taken from dates across it, so a seasonal cycle does not
    saturate the map.
- **Out.** A raster exports to NetCDF with every time step and its CRS as a grid_mapping. An
  export says when it now states a CRS that was assumed at load.
- The demo gains a monthly temperature grid with a warming trend of exactly 0.3 °C per decade,
  hidden by an ±8 K seasonal cycle in kelvin. A network test reads a day of the real ERA5
  archive over Sydney.

`load`'s new `select=` and the band summary's units are changes to the stable interface.

**Alternatives.**
- Read every variable and dimension and let scripts choose: a store like ERA5 cannot be opened
  that way, and silent choices of a level or time would be guesses.
- h5netcdf instead of netCDF4: no NetCDF 3 files, which older data still use.
- Reproject 0–360 grids to -180–180 at load: it changes the data; only the map needs it.
- obstore for cloud stores: smaller, but fsspec is already a dependency and is the ecosystem's
  common path.

