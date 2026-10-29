"""`loci-app`: the map interface and its API, served locally (D-043).

    uv run loci-app                      # http://127.0.0.1:8765, workspace from Workspace.from_env
    uv run loci-app --workspace path/.loci --port 9000

The API is JSON under /api; the built web app (app/dist) is served at /. Requests run the
blocking work in a thread so map tiles keep flowing while a script runs.
"""

import argparse
import hashlib
import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from loci import provenance
from loci.errors import LociError, UnknownRefError
from loci.tools.lineage import lineage
from loci.tools.run import run
from loci.tools.validate import validate
from loci.web import layers, project
from loci.web.agent import DEFAULT_MODEL, MODELS, Agent
from loci.workspace import Workspace

DEFAULT_PORT = 8765
DIST_ENV = "LOCI_APP_DIST"
EMPTY_TILE = Response(status_code=204)
EVENT_INTERVAL = 1.0  # seconds between checks for workspace changes


def create_app(
    ws: Workspace,
    *,
    project_dir: Path | None = None,
    dist: Path | None = None,
    agent: Agent | None = None,
) -> Starlette:
    """The Starlette app for one workspace.

    `project_dir` holds analysis/ (default: the workspace's parent folder), `dist` is the built
    web app to serve, and `agent` answers chat messages (default: Claude over the loci tools).
    """
    routes: list[Route | Mount] = [
        Route("/api/project", _project),
        Route("/api/events", _events),
        Route("/api/models", _models),
        Route("/api/agent", _agent, methods=["POST"]),
        Route("/api/scripts", _list_scripts),
        Route("/api/scripts/{path:path}", _script, methods=["GET", "PUT"]),
        Route("/api/run", _run, methods=["POST"]),
        Route("/api/layers", _list_layers),
        Route("/api/layers/{ref}", _layer_detail),
        Route("/api/layers/{ref}/display", _layer_display),
        Route("/api/layers/{ref}/tiles/{z:int}/{x:int}/{y:int}.png", _layer_tile),
        Route("/api/layers/{ref}/geojson", _layer_geojson),
        Route("/api/layers/{ref}/point", _layer_point),
        Route("/api/layers/{ref}/validate", _layer_validate, methods=["POST"]),
        Route("/api/previews/{name}", _preview_file),
    ]
    if dist is not None and (dist / "index.html").exists():
        routes.append(Mount("/", StaticFiles(directory=dist, html=True)))
    app = Starlette(
        routes=routes,
        exception_handlers={UnknownRefError: _not_found, LociError: _bad_request},
    )
    app.state.ws = ws
    app.state.project = (project_dir or ws.root.parent).resolve()
    app.state.agent = agent or Agent(ws)
    return app


def _ws(request: Request) -> Workspace:
    ws: Workspace = request.app.state.ws
    return ws


async def _json(work: Callable[[], Any]) -> JSONResponse:
    """Run blocking work in a thread and return its result as JSON."""
    result = await run_in_threadpool(work)
    if isinstance(result, BaseModel):
        return JSONResponse(result.model_dump(mode="json"))
    if isinstance(result, list):
        return JSONResponse([r.model_dump(mode="json") for r in result])
    return JSONResponse(result)


async def _list_layers(request: Request) -> Response:
    return await _json(lambda: layers.layers(_ws(request)))


async def _layer_detail(request: Request) -> Response:
    ws, ref = _ws(request), request.path_params["ref"]

    def detail() -> dict[str, Any]:
        record = provenance.read(ws, ref)
        return {
            "layer": layers.layer_info(ws, ws.record(ref)).model_dump(mode="json"),
            "lineage": lineage(ws, ref).model_dump(mode="json"),
            "validation": record.validation.model_dump(mode="json")
            if record and record.validation
            else None,
        }

    return await _json(detail)


async def _layer_display(request: Request) -> Response:
    ws, ref = _ws(request), request.path_params["ref"]
    bands = _bands(request.query_params.get("bands"))
    time = request.query_params.get("time") or None
    return await _json(lambda: layers.display(ws, ref, bands=bands, time=time))


async def _layer_tile(request: Request) -> Response:
    ws, p, q = _ws(request), request.path_params, request.query_params
    bands = _bands(q.get("bands")) or []
    stretch = (_number(q["lo"]), _number(q["hi"])) if "lo" in q and "hi" in q else None
    step = int(_number(q["step"])) if "step" in q else None
    outside = _range(q["outside"]) if "outside" in q else None
    png = await run_in_threadpool(
        layers.tile,
        ws,
        p["ref"],
        p["z"],
        p["x"],
        p["y"],
        bands=bands,
        stretch=stretch,
        step=step,
        outside=outside,
    )
    if png is None:
        return EMPTY_TILE
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


async def _layer_geojson(request: Request) -> Response:
    ws, ref = _ws(request), request.path_params["ref"]
    text = await run_in_threadpool(layers.geojson, ws, ref)
    return Response(text, media_type="application/geo+json")


async def _layer_point(request: Request) -> Response:
    ws, ref, q = _ws(request), request.path_params["ref"], request.query_params
    try:
        lon, lat = float(q["lon"]), float(q["lat"])
    except (KeyError, ValueError) as e:
        raise LociError("Give the point as lon= and lat= in degrees.") from e
    return await _json(lambda: layers.point_values(ws, ref, lon, lat))


async def _layer_validate(request: Request) -> Response:
    ws, ref = _ws(request), request.path_params["ref"]
    return await _json(lambda: validate(ws, [ref]))


async def _preview_file(request: Request) -> Response:
    name = request.path_params["name"]
    path = _ws(request).previews_dir / name
    if Path(name).name != name or path.suffix != ".png" or not path.exists():
        return JSONResponse({"error": f"No preview named {name!r}."}, status_code=404)
    return FileResponse(path, media_type="image/png")


async def _project(request: Request) -> Response:
    return JSONResponse(
        {
            "project": str(request.app.state.project),
            "workspace": str(_ws(request).root),
            "scripts": project.SCRIPTS_DIR,
        }
    )


async def _events(request: Request) -> Response:
    """Server-sent events: `layers` whenever data_refs or their records change.

    ?once=1 sends the current state and ends, which tests and scripts use.
    """
    ws, once = _ws(request), request.query_params.get("once") == "1"

    async def stream() -> AsyncIterator[str]:
        last = None
        while True:
            current = await run_in_threadpool(workspace_fingerprint, ws)
            if current != last:
                yield f"event: layers\ndata: {json.dumps({'fingerprint': current})}\n\n"
                last = current
            if once or await request.is_disconnected():
                return
            await anyio.sleep(EVENT_INTERVAL)

    return StreamingResponse(stream(), media_type="text/event-stream")


def workspace_fingerprint(ws: Workspace) -> str:
    """Changes whenever a data_ref is added or removed, or its provenance is updated."""
    files = sorted([*ws.refs_dir.glob("*.json"), *ws.provenance_dir.glob("*.json")])
    digest = hashlib.sha256()
    for file in files:
        digest.update(f"{file.name}:{file.stat().st_mtime_ns};".encode())
    return digest.hexdigest()[:16]


async def _models(request: Request) -> Response:
    return JSONResponse({"models": MODELS, "default": DEFAULT_MODEL})


async def _agent(request: Request) -> Response:
    """One chat message, answered as a stream of server-sent events (see loci.web.agent)."""
    body = await _body(request)
    message = str(body.get("message", "")).strip()
    if not message:
        raise LociError("Send a message for the agent.")
    agent: Agent = request.app.state.agent
    events = agent.send(
        message,
        model=str(body.get("model") or DEFAULT_MODEL),
        session_id=body.get("session_id") or None,
    )

    async def stream() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _list_scripts(request: Request) -> Response:
    return await _json(lambda: project.scripts(request.app.state.project))


async def _script(request: Request) -> Response:
    root, path = request.app.state.project, request.path_params["path"]
    if request.method == "PUT":
        content = str((await _body(request)).get("content", ""))
        return await _json(lambda: project.write_script(root, path, content))
    return await _json(lambda: project.read_script(root, path))


async def _run(request: Request) -> Response:
    """Run a project script with declared inputs, as the run tool does."""
    ws, root, body = _ws(request), request.app.state.project, await _body(request)
    script = project.script_path(root, str(body.get("path", "")))
    inputs = body.get("inputs") or {}
    parameters = body.get("parameters") or None
    timeout = float(body.get("timeout") or 600)
    return await _json(
        lambda: run(ws, path=str(script), inputs=inputs, parameters=parameters, timeout=timeout)
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as e:
        raise LociError("The request body must be JSON.") from e
    if not isinstance(body, dict):
        raise LociError("The request body must be a JSON object.")
    return body


def _bands(value: str | None) -> list[str] | None:
    return [b for b in value.split(",") if b] if value else None


def _number(value: str) -> float:
    try:
        return float(value)
    except ValueError as e:
        raise LociError(f"Not a number: {value!r}.") from e


def _range(value: str) -> tuple[float, float]:
    """'low,high' as two numbers; 'inf' and '-inf' stand for an open end."""
    parts = value.split(",")
    if len(parts) != 2:
        raise LociError(f"A range is two numbers, low,high: {value!r}.")
    return _number(parts[0]), _number(parts[1])


async def _not_found(request: Request, exc: Exception) -> Response:
    return _error(exc, 404)


async def _bad_request(request: Request, exc: Exception) -> Response:
    return _error(exc, 400)


def _error(exc: Exception, status: int) -> Response:
    hint = exc.hint if isinstance(exc, LociError) else None
    message = exc.message if isinstance(exc, LociError) else str(exc)
    return JSONResponse({"error": message, "hint": hint}, status_code=status)


def default_dist() -> Path:
    """The built web app: $LOCI_APP_DIST, or app/dist in the repository."""
    if value := os.environ.get(DIST_ENV):
        return Path(value)
    return Path(__file__).resolve().parents[3] / "app" / "dist"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="loci-app", description="The loci map interface.")
    parser.add_argument("--workspace", type=Path, help="workspace folder (default: .loci)")
    parser.add_argument("--project", type=Path, help="project folder with analysis/")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    ws = Workspace(args.workspace) if args.workspace else Workspace.from_env()
    app = create_app(ws, project_dir=args.project, dist=default_dist())
    print(f"loci: workspace {ws.root}; open http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
