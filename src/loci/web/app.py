"""`loci-app`: the map interface and its API, served locally (D-043). `loci open` starts it too.

    uv run loci-app                      # http://127.0.0.1:8765, workspace from Workspace.from_env
    uv run loci-app --workspace path/.loci --port 9000

The API is JSON under /api; the built web app (static/, from npm run build) is served at /.
Requests run the blocking work in a thread so map tiles keep flowing while a script runs. Only
this computer's own pages may use it (D-049): a request for another host name is refused, and so
is any change asked by a page from another origin.
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
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from loci import demo, provenance
from loci.errors import LociError, UnknownRefError
from loci.io.write import RASTER_FORMATS, VECTOR_FORMATS
from loci.tools.export import export
from loci.tools.lineage import lineage
from loci.tools.load import load
from loci.tools.run import run
from loci.tools.validate import validate
from loci.web import files, layers, project
from loci.web.agent import Agent
from loci.web.providers import BY_ID, DEFAULT_MODEL, forget_key, providers, save_key
from loci.web.turn import DEFAULT_REVIEW
from loci.workspace import Workspace

DEFAULT_PORT = 8765
DIST_ENV = "LOCI_APP_DIST"
STATIC = Path(__file__).parent / "static"  # the built web app (npm run build in app/)
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
EMPTY_TILE = Response(status_code=204)
EVENT_INTERVAL = 1.0  # seconds between checks for workspace changes


def create_app(
    ws: Workspace,
    *,
    project_dir: Path | None = None,
    dist: Path | None = None,
    agent: Agent | None = None,
    hosts: tuple[str, ...] = LOCAL_HOSTS,
) -> Starlette:
    """The Starlette app for one workspace.

    `project_dir` holds analysis/ (default: the workspace's parent folder), `dist` is the built
    web app to serve, `agent` answers chat messages (default: the models set up for this user,
    over the loci tools), and `hosts` are the host names it answers to.
    """
    routes: list[Route | Mount] = [
        Route("/api/project", _project),
        Route("/api/events", _events),
        Route("/api/models", _models),
        Route("/api/providers/{provider}/key", _provider_key, methods=["PUT", "DELETE"]),
        Route("/api/agent", _agent, methods=["POST"]),
        Route("/api/agent/decide", _decide, methods=["POST"]),
        Route("/api/runs/{run_id}/script", _run_script),
        Route("/api/files", _list_files),
        Route("/api/files/load", _load_file, methods=["POST"]),
        Route("/api/files/data/{name}", _upload, methods=["PUT"]),
        Route("/api/exports/{name}", _exported),
        Route("/api/layers/{ref}/export", _export_layer, methods=["POST"]),
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
        middleware=[Middleware(LocalOnly, hosts=hosts)],
        exception_handlers={UnknownRefError: _not_found, LociError: _bad_request},
    )
    app.state.ws = ws
    app.state.project = (project_dir or ws.root.parent).resolve()
    app.state.agent = agent or Agent(ws, project=app.state.project)
    return app


class LocalOnly:
    """Refuse what a web page elsewhere could ask of this local app: any request for another host
    name (DNS rebinding), and any change asked by a page from another origin."""

    def __init__(self, app: ASGIApp, hosts: tuple[str, ...]) -> None:
        self.app = app
        self.hosts = hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (problem := self._refused(scope)):
            await JSONResponse({"error": problem}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _refused(self, scope: Scope) -> str | None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        if _hostname(host) not in self.hosts:
            return f"loci answers only to this computer's own pages, not to {host!r}."
        origin = headers.get("origin")
        if scope["method"] not in SAFE_METHODS and origin not in (None, f"http://{host}"):
            return "loci takes changes only from its own pages."
        return None


def _hostname(host: str) -> str:
    """A Host header without its port: "127.0.0.1:8765" -> "127.0.0.1", "[::1]:80" -> "::1"."""
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _ws(request: Request) -> Workspace:
    ws: Workspace = request.app.state.ws
    return ws


async def _json(work: Callable[[], Any]) -> Response:
    """Run blocking work in a thread and return its result as JSON. Models serialise themselves,
    so a NaN, such as a band's nodata, travels as "NaN" as it does over MCP."""
    result = await run_in_threadpool(work)
    if isinstance(result, BaseModel):
        return Response(result.model_dump_json(), media_type="application/json")
    if isinstance(result, list) and all(isinstance(r, BaseModel) for r in result):
        body = "[" + ",".join(r.model_dump_json() for r in result) + "]"
        return Response(body, media_type="application/json")
    return JSONResponse(result)


async def _list_layers(request: Request) -> Response:
    return await _json(lambda: layers.layers(_ws(request)))


async def _layer_detail(request: Request) -> Response:
    ws, ref = _ws(request), request.path_params["ref"]

    def detail() -> dict[str, Any]:
        record = provenance.read(ws, ref)
        link = project.script_link(request.app.state.project, record) if record else None
        return {
            "layer": layers.layer_info(ws, ws.record(ref)).model_dump(mode="json"),
            "lineage": lineage(ws, ref).model_dump(mode="json"),
            "validation": record.validation.model_dump(mode="json")
            if record and record.validation
            else None,
            "script": link.model_dump(mode="json") if link else None,
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
            "suggestions": demo.suggestions(request.app.state.project),
        }
    )


async def _events(request: Request) -> Response:
    """Server-sent events: `layers` whenever data_refs or their records change, and `scripts`
    whenever a script under analysis/ is added, removed or saved (by the agent too).

    ?once=1 sends the current state and ends, which tests and scripts use.
    """
    ws, once = _ws(request), request.query_params.get("once") == "1"
    folder: Path = request.app.state.project

    async def stream() -> AsyncIterator[str]:
        last: dict[str, str] = {}
        while True:
            current = {
                "layers": await run_in_threadpool(workspace_fingerprint, ws),
                "scripts": await run_in_threadpool(project.scripts_fingerprint, folder),
            }
            for kind, value in current.items():
                if last.get(kind) != value:
                    yield f"event: {kind}\ndata: {json.dumps({'fingerprint': value})}\n\n"
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
    """The model picker's choices: every provider, whether it is set up, and its models."""
    agent: Agent = request.app.state.agent

    def listed() -> dict[str, Any]:
        found = providers(agent.settings())
        return {"providers": [p.model_dump(mode="json") for p in found], "default": DEFAULT_MODEL}

    return await _json(listed)


async def _provider_key(request: Request) -> Response:
    """Save or forget a provider's API key; the reply lists the providers, never a key."""
    agent: Agent = request.app.state.agent
    name = request.path_params["provider"]
    provider = BY_ID.get(name)
    if provider is None:
        raise LociError(f"No provider {name!r}.", hint=f"Providers: {', '.join(BY_ID)}.")
    if request.method == "PUT":
        key = (await _body(request)).get("api_key")
        if not isinstance(key, str):
            raise LociError("Send the key as api_key.")
        await run_in_threadpool(lambda: save_key(provider, key, agent.settings()))
    else:
        await run_in_threadpool(lambda: forget_key(provider, agent.settings()))
    return await _models(request)


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
        review=str(body.get("review") or DEFAULT_REVIEW),
    )

    async def stream() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _decide(request: Request) -> Response:
    """The scientist's answer to a change or run the agent is waiting on."""
    body = await _body(request)
    agent: Agent = request.app.state.agent
    accepted = body.get("accepted")
    if not isinstance(accepted, bool):
        raise LociError("Answer with accepted: true or false.")
    if not agent.decide(str(body.get("id", "")), accepted, str(body.get("note") or "")):
        return JSONResponse({"error": "Nothing is waiting for that answer."}, status_code=404)
    return JSONResponse({"id": body["id"], "accepted": accepted})


async def _list_files(request: Request) -> Response:
    return await _json(lambda: files.data_files(request.app.state.project, _ws(request)))


async def _load_file(request: Request) -> Response:
    """Put a project file on the map, as the load tool does, and check it at once."""
    ws, body = _ws(request), await _body(request)
    file = files.project_file(request.app.state.project, str(body.get("path", "")))

    def loaded() -> BaseModel:
        result = load(ws, str(file))
        validate(ws, [result.data_ref])  # as the hook does for the agent's loads
        return result

    return await _json(loaded)


async def _upload(request: Request) -> Response:
    """Save a file dropped on the map in data/; ?replace=1 replaces one already there."""
    name = request.path_params["name"]
    replace = request.query_params.get("replace") == "1"
    try:
        saved = await files.save_upload(
            request.app.state.project, name, request.stream(), replace=replace
        )
    except FileExistsError as e:
        return JSONResponse({"error": str(e), "hint": "Replace it, or rename the file."}, 409)
    path = saved.path.relative_to(request.app.state.project).as_posix()
    loadable = saved.path.suffix.lower() in files.LISTED
    return JSONResponse({"path": path, "loadable": loadable, "unchanged": saved.unchanged})


async def _export_layer(request: Request) -> Response:
    """Write a layer to exports/ as it is held; the reply says where, and what changed."""
    ws, ref, body = _ws(request), request.path_params["ref"], await _body(request)
    fmt = str(body.get("format") or "")
    step = body.get("step")
    suffix = {**RASTER_FORMATS, **VECTOR_FORMATS}.get(fmt)
    if suffix is None or (step is not None and not isinstance(step, int)):
        raise LociError("Choose a format, and a time step as a whole number.")
    name = layers.layer_info(ws, ws.record(ref)).name
    target = files.export_path(request.app.state.project, name, suffix)
    result = await run_in_threadpool(export, ws, ref, str(target), format=fmt, step=step)
    return JSONResponse({**result.model_dump(mode="json"), "download": target.name})


async def _exported(request: Request) -> Response:
    name = request.path_params["name"]
    path = request.app.state.project / files.EXPORTS_DIR / name
    if Path(name).name != name or name.startswith(".") or not path.is_file():
        return JSONResponse({"error": f"No export named {name!r}."}, status_code=404)
    return FileResponse(path, filename=name)


async def _run_script(request: Request) -> Response:
    ws, run_id = _ws(request), request.path_params["run_id"]
    return await _json(lambda: project.run_script(ws, run_id))


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
    """The built web app: $LOCI_APP_DIST, or the copy in this package."""
    if value := os.environ.get(DIST_ENV):
        return Path(value)
    return STATIC


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="loci-app", description="The loci map interface.")
    parser.add_argument("--workspace", type=Path, help="workspace folder (default: .loci)")
    parser.add_argument("--project", type=Path, help="project folder with analysis/")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    ws = Workspace(args.workspace) if args.workspace else Workspace.from_env()
    # On a wildcard address loci still answers only to this computer's own names (LocalOnly).
    wildcard = args.host in (*LOCAL_HOSTS, "0.0.0.0", "::")
    hosts = LOCAL_HOSTS if wildcard else (args.host, *LOCAL_HOSTS)
    app = create_app(ws, project_dir=args.project, dist=default_dist(), hosts=hosts)
    print(f"loci: workspace {ws.root}; open http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
