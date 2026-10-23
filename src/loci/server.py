"""The MCP server: loci's tools for Claude Code, over stdio.

A thin adapter (D-037). Each MCP tool is a function from loci.tools with the Workspace filled in,
so its arguments, description and result schema are exactly those of the Python tool. Errors come
back as tool errors the agent can read, and preview also returns its picture.

Start it with `uv run loci-mcp`; the project's .mcp.json does that for Claude Code.
"""

import base64
import sys
import threading
import traceback
from collections.abc import Callable
from functools import cache
from inspect import signature
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ContentBlock, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel

import loci
from loci.errors import LociError
from loci.models import PreviewResult
from loci.tools.inspect import inspect
from loci.tools.lineage import lineage
from loci.tools.load import load
from loci.tools.preview import preview
from loci.tools.run import run
from loci.tools.stac_search import stac_search
from loci.tools.validate import validate
from loci.workspace import Workspace

INSTRUCTIONS = """\
loci runs geospatial analysis that a scientist can trust and trace.

- Data stays on disk. Tools return a data_ref (such as raster-3f9a2c1b) with a compact summary;
  pass data_refs to later tools. Do not read data files into the conversation.
- A usual order: stac_search or load, then inspect, validate, preview, run, and lineage.
- Nothing is reprojected, resampled or dropped unless you ask (for example resolution= or
  output_crs= in load). Assumptions come back as warnings: pass them on to the user.
- Validate before drawing conclusions, with every data_ref that will be combined in one call.
  Report warn and fail results with their hints; do not hide them.
- run executes Python for anything the tools do not do. Read data only with
  loci.script.read(name) for inputs declared in run(inputs={name: data_ref}), so provenance lists
  them; it gives physical values (scale and offset applied, nodata as NaN). Save results with
  loci.script.save(obj, "name.tif"); they are registered and validated automatically.
- lineage traces a data_ref, or a file written by run, back to its original sources.
"""

Tool = Callable[..., BaseModel]
TOOLS: list[tuple[Tool, str, ToolAnnotations]] = [
    (stac_search, "Search a STAC catalogue", ToolAnnotations(open_world_hint=True)),
    (load, "Load a file, URL or STAC search", ToolAnnotations(open_world_hint=True)),
    (inspect, "Describe a data_ref", ToolAnnotations(read_only_hint=True)),
    (validate, "Check data_refs for geospatial problems", ToolAnnotations()),
    (preview, "Draw a data_ref as a small picture", ToolAnnotations()),
    (run, "Run a Python script on data_refs", ToolAnnotations()),
    (lineage, "Trace a result back to its sources", ToolAnnotations(read_only_hint=True)),
]

# The SDK runs synchronous tools in worker threads. One call at a time keeps the shared workspace
# (its open-data cache, GDAL handles) free of races; calls are not meant to overlap anyway.
_lock = threading.Lock()


def build_server(ws: Workspace | None = None) -> MCPServer:
    """The loci MCP server, on `ws` or, by default, the workspace from Workspace.from_env()."""
    workspace = (lambda: ws) if ws is not None else cache(Workspace.from_env)
    server = MCPServer(name="loci", instructions=INSTRUCTIONS, version=loci.__version__)
    for tool, title, annotations in TOOLS:
        server.add_tool(
            _adapt(tool, workspace), title=title, annotations=annotations, structured_output=True
        )
    return server


def _adapt(tool: Tool, workspace: Callable[[], Workspace]) -> Callable[..., CallToolResult]:
    """`tool` without its Workspace parameter: same name, arguments, docstring and result model."""
    sig = signature(tool, eval_str=True)

    def adapted(**arguments: Any) -> CallToolResult:
        return call(tool, workspace(), arguments)

    adapted.__name__ = tool.__name__
    adapted.__doc__ = tool.__doc__
    adapted.__signature__ = sig.replace(  # type: ignore[attr-defined]
        parameters=list(sig.parameters.values())[1:],
        return_annotation=Annotated[CallToolResult, sig.return_annotation],
    )
    return adapted


def call(tool: Tool, ws: Workspace, arguments: dict[str, Any]) -> CallToolResult:
    """Run a tool and package its result; failures become tool errors the agent can read."""
    try:
        with _lock:
            result = tool(ws, **arguments)
    except LociError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise ToolError(f"{type(e).__name__}: {e}") from e
    return _package(result)


def _package(result: BaseModel) -> CallToolResult:
    """The result as compact JSON text and as structured content; previews add their picture."""
    content: list[ContentBlock] = [
        TextContent(type="text", text=result.model_dump_json(exclude_none=True))
    ]
    if isinstance(result, PreviewResult):
        png = base64.b64encode(Path(result.path).read_bytes()).decode()
        content.append(ImageContent(type="image", data=png, mime_type="image/png"))
    return CallToolResult(content=content, structured_content=result.model_dump(mode="json"))


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
