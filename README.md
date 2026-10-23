# loci

Engine for an agent harness that helps scientists run geospatial workflows they can trust.

Scientists pose questions about places. Agents gather evidence, test it and keep it current. This
first phase builds only the engine: a Python package exposed as an MCP server and used from Claude
Code, with verification (`validate`) and provenance (`lineage`) built in from the start. Large data
stays in a workspace on disk. Tools pass around compact summaries and `data_ref` handles instead.

Status: early development. See the milestone list in [`CLAUDE.md`](CLAUDE.md).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 if it is missing.

```sh
uv sync            # create .venv with the package and dev tools
uv run pytest      # run the test suite
```

The tools are plain Python functions for now. The MCP server arrives in M5.

```python
from loci.workspace import Workspace
from loci.tools.load import load
from loci.tools.preview import preview
from loci.tools.stac_search import stac_search
from loci.tools.inspect import inspect
from loci.tools.lineage import lineage
from loci.tools.run import run
from loci.tools.validate import validate

ws = Workspace(".loci")
dem = load(ws, "data/dem.tif")  # data_ref plus a header summary; no pixels read
print(inspect(ws, dem.data_ref).model_dump_json(indent=2))  # adds bounded per-band statistics

samples = load(ws, "data/samples.csv")  # lon/lat columns detected; any assumption is a warning
print(samples.warnings)

report = validate(ws, [dem.data_ref, samples.data_ref])  # also checked against each other
for check in report.checks:
    print(check.status, check.check, check.message)

# Sentinel-2 over Sydney Harbour: search, load pixels, look at them
found = stac_search(
    ws, bbox=[151.15, -33.90, 151.30, -33.80], start="2024-06-01", end="2024-06-30", max_cloud=20
)
stack = load(ws, found.data_ref, bands=["red", "green", "blue"])
print(preview(ws, stack.data_ref).path)  # PNG of the least cloudy day

# Your own analysis, in its own process; outputs are registered, validated and traced
NDVI = """
from loci import script
stack = script.read("stack")
red, nir = script.physical(stack["red"]), script.physical(stack["nir"])
script.save(((nir - red) / (nir + red)).median("time").rename("ndvi"), "ndvi.tif")
"""
bands = load(ws, found.data_ref, bands=["red", "nir"], drop_offset=True)  # see D-030
result = run(ws, code=NDVI, inputs={"stack": bands.data_ref})
ndvi = result.outputs[0]  # a data_ref, already validated, with a provenance record
for step in lineage(ws, ndvi.data_ref).steps:  # run <- load <- stac_search <- Earth Search
    print(step.produced_by, step.description)
```

## Documentation

- [`CLAUDE.md`](CLAUDE.md): project guide, commands, rules and milestones.
- [`docs/DECISIONS.md`](docs/DECISIONS.md): design decisions, with the alternatives considered.
