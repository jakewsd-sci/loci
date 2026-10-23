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
