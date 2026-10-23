"""Geospatial invariants behind the `validate` tool.

Each check is a small function that takes one or more `Layer`s and returns a `CheckResult`. Checks
read data and statistics samples directly and know nothing about the workspace or MCP; `runner`
decides which checks apply to which inputs.
"""
