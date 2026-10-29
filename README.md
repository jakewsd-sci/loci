# loci

Engine for an agent harness that helps scientists run geospatial workflows they can trust.

Scientists pose questions about places. Agents gather evidence, test it and keep it current.
loci is a Python package with verification (`validate`) and provenance (`lineage`) built in, used
from Claude Code as an MCP server or from its own map app, where an agent writes each analysis as
a script you review. Large data stays in a workspace on disk. Tools pass around compact summaries
and `data_ref` handles instead.

Status: early development. See the milestone list in [`CLAUDE.md`](CLAUDE.md).

## Install

**Windows**, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/jakewsd-sci/loci/main/install.ps1 | iex"
```

**macOS or Linux**:

```sh
curl -LsSf https://raw.githubusercontent.com/jakewsd-sci/loci/main/install.sh | sh
```

The script installs [uv](https://docs.astral.sh/uv/) if needed, and uv installs Python 3.12 and
loci's packages, at the versions loci was tested with, into a folder of their own. Then, in a new
terminal:

```sh
loci demo                       # a demo project with known answers, opened in your browser
loci open path/to/your/data     # the map app on a folder of your own data
loci doctor                     # check the installation
```

The first time, open the model menu (top right) and paste an API key: an
[Anthropic key](https://platform.claude.com/settings/keys) for Claude, or a key for OpenAI,
Google Gemini or OpenRouter. It is kept in your own settings on your computer, never in the
project. `uv tool uninstall loci` removes loci.

## Develop

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 if it is missing.

```sh
uv sync            # create .venv with the package and dev tools
uv run pytest      # run the test suite
```

The tools are plain Python functions, also served over MCP (below).

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
red, nir = stack["red"], stack["nir"]  # physical values: scale, offset, NaN nodata
script.save(((nir - red) / (nir + red)).median("time").rename("ndvi"), "ndvi.tif")
"""
bands = load(ws, found.data_ref, bands=["red", "nir"], drop_offset=True)  # see D-030
result = run(ws, code=NDVI, inputs={"stack": bands.data_ref})
ndvi = result.outputs[0]  # a data_ref, already validated, with a provenance record
for step in lineage(ws, ndvi.data_ref).steps:  # run <- load <- stac_search <- Earth Search
    print(step.produced_by, step.description)
```

## Use from Claude Code

Open Claude Code in this folder and trust the folder when asked. The project's `.mcp.json` starts
the `loci` MCP server with `uv run loci-mcp`, and its tools appear as `mcp__loci__load`,
`mcp__loci__validate` and so on. Then ask a question, for example:

> How green was the land around Sydney Harbour (bbox 151.15,-33.90,151.30,-33.80) in June 2024?
> Give the median NDVI and say how far the answer can be trusted.

Every data_ref that `load`, `stac_search` or `run` makes is validated automatically by a hook in
`.claude/settings.json`, and the result is shown to Claude next to the tool's output. To make a
failed validation stop Claude until it is dealt with, or to turn the hook off, set
`LOCI_VALIDATION_HOOK` to `block` or `off` in `.claude/settings.local.json`:

```json
{ "env": { "LOCI_VALIDATION_HOOK": "block" } }
```

## The map app

`loci open` serves a local web app over a folder's workspace: a map to explore every layer, an
agent to ask (Claude, or a model from OpenAI, Google Gemini or OpenRouter), the scripts it runs,
and each layer's lineage and checks.
Step through the dates of a time stack on one colour scale, click a place to chart its values
through time (cloudy dates are marked, not hidden), draw a box to ask the agent about an area,
and show a failed check on the map to see where the problem lies.

The agent writes each analysis as a script in `analysis/`, where you can read, edit and run it.
When it wants to change a script you already have, it waits and shows you the change as a diff
to accept or reject; set the composer to "Review every run" to approve each run as well. Every
result links to its script, and to the exact version that made it.
From a clone, build the app once with [Node 22](https://nodejs.org/); it is bundled into the
package, which serves it:

```sh
cd app && npm install && npm run build && cd ..
uv run loci open             # then open the address it prints
```

For work on the app itself, run `npm run dev` in `app/` next to `uv run loci-app`, and open the
address Vite prints. The app answers only to pages from this computer, and takes changes only
from its own pages.

### Models

Claude (Opus, Sonnet or Haiku) answers in Claude Code, which comes with loci. Models from
OpenAI, Google Gemini and OpenRouter answer in loci's own agent loop, with the same tools,
scripts, reviews and automatic checks. Pick one from the model menu once its provider has a key.

Paste a key into the model menu, or set it before starting loci (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`). Claude takes an
Anthropic API key, as Anthropic asks of apps built on its Agent SDK, or reaches Claude through
Amazon Bedrock, Google Vertex AI or Microsoft Foundry. Keys pasted into the app go in
`keys.toml` in your settings folder (`%APPDATA%\loci` on Windows, `~/.config/loci` elsewhere),
readable by you only. Beside it, your own `config.toml` can hold keys too, list models and pick
Claude's route; loci reads both files for every message and never writes `config.toml`.

```toml
[openai]
api_key = "sk-..."

[openrouter]
api_key = "sk-or-..."
models = ["moonshotai/kimi-k3", { id = "qwen/qwen3.8-flash", label = "Qwen 3.8 Flash", note = "Open weights" }]

[claude]
route = "bedrock"                    # or vertex or foundry; anthropic by default
env = { AWS_REGION = "us-east-1" }   # whatever else that route needs
```

A conversation stays with the kind of model that began it. Switching between Claude and another
provider starts a new conversation, and the app says so before you send. OpenAI, Gemini and
OpenRouter models can carry on each other's conversations.

## Benchmark

Five geospatial questions on synthetic data with exactly known answers. Each has a trap that makes
a careless answer wrong: cloud in a time series, layers in different CRSs, areas in degrees, a
point table with no stated CRS, and slope on a grid in degrees.

```sh
uv run python benchmarks/run.py           # reference solutions through loci's tools
uv run python benchmarks/run.py --agent   # Claude Code answers each task (costs model usage)
```

Each attempt keeps its data, workspace and transcript under `benchmarks/results/`, so every
answer can be traced with `lineage`.

## Documentation

- [`CLAUDE.md`](CLAUDE.md): project guide, commands, rules and milestones.
- [`docs/DECISIONS.md`](docs/DECISIONS.md): design decisions, with the alternatives considered.
