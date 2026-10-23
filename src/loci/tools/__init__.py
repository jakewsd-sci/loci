"""Tool functions: a Workspace plus plain arguments in, a pydantic result model out.

These signatures are the stable interface that the MCP server (M5) and a future UI wrap. Import
each tool from its module (`from loci.tools.load import load`); re-exporting them here would
shadow the modules of the same name.
"""
