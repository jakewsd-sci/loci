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

## Documentation

- [`CLAUDE.md`](CLAUDE.md): project guide, commands, rules and milestones.
- [`docs/DECISIONS.md`](docs/DECISIONS.md): design decisions, with the alternatives considered.
