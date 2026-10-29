"""`loci`: the map app on a folder of your data, a demo project, and a check of the installation.

    loci open [FOLDER]    open the app on FOLDER (default: the current folder)
    loci demo [FOLDER]    write a demo project with known answers (default: ~/loci-demo), open it
    loci doctor           check that loci is installed and set up
    loci mcp              the MCP server for Claude Code, over stdio

A folder's workspace is FOLDER/.loci and its scripts go in FOLDER/analysis, so a project is a
folder you can keep, share or put under version control.
"""

import argparse
import platform
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

import loci
from loci import demo
from loci.errors import LociError
from loci.web.app import DEFAULT_PORT, create_app, default_dist
from loci.web.providers import PROVIDERS, configured, key_name, key_source, load_settings
from loci.workspace import Workspace

PORTS_TRIED = 20  # from the default port up, the first free one is used


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="loci", description="Geospatial analysis you can trust and trace, on a map."
    )
    parser.add_argument("--version", action="version", version=f"loci {loci.__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    opening = commands.add_parser("open", help="open the map app on a folder of data")
    opening.add_argument("folder", nargs="?", default=".", help="default: the current folder")
    demoing = commands.add_parser("demo", help="write a demo project with known answers, open it")
    demoing.add_argument("folder", nargs="?", default=str(Path.home() / "loci-demo"))
    demoing.add_argument("--no-open", action="store_true", help="only write the project")
    for command in (opening, demoing):
        command.add_argument("--port", type=int, default=DEFAULT_PORT)
        command.add_argument("--no-browser", action="store_true", help="do not open a browser")
    commands.add_parser("doctor", help="check that loci is installed and set up")
    commands.add_parser("mcp", help="the MCP server for Claude Code, over stdio")
    args = parser.parse_args(argv)

    try:
        if args.command == "open":
            serve(_folder(args.folder), port=args.port, browser=not args.no_browser)
        elif args.command == "demo":
            folder = make_demo(Path(args.folder).expanduser().resolve())
            if not args.no_open:
                serve(folder, port=args.port, browser=not args.no_browser)
        elif args.command == "doctor":
            sys.exit(0 if doctor() else 1)
        else:
            from loci.server import main as serve_mcp

            serve_mcp()
    except LociError as e:
        sys.exit(f"loci: {e}")


def serve(folder: Path, *, port: int, browser: bool) -> None:
    """Run the app on `folder` until Ctrl+C; open a browser on it once it answers."""
    app = create_app(Workspace(folder / ".loci"), project_dir=folder, dist=default_dist())
    port = _free_port(port)
    url = f"http://127.0.0.1:{port}"
    print(f"loci {loci.__version__} on {folder}\n  {url}   (Ctrl+C to stop)", flush=True)
    if browser:
        threading.Thread(target=_open_when_ready, args=(port, url), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def make_demo(folder: Path) -> Path:
    """Write the demo project into `folder`, which must be new, empty or an earlier demo."""
    if folder.exists() and any(folder.iterdir()) and not demo.is_demo(folder):
        raise LociError(
            f"{folder} already holds other files.",
            hint="Give an empty or new folder: loci demo <folder>.",
        )
    written = demo.write_demo(folder)
    print(f"Demo project in {folder}" + (f": wrote {len(written)} files." if written else "."))
    return folder


def doctor() -> bool:
    """Print what loci needs and whether it is there; False if something essential is missing."""
    import rasterio
    from pyproj import proj_version_str

    ok = True

    def line(good: bool, text: str, *, essential: bool = True) -> None:
        nonlocal ok
        ok = ok and (good or not essential)
        print(f"  [{'ok' if good else '!!' if essential else '--'}] {text}")

    print(f"loci {loci.__version__} on Python {platform.python_version()}, {platform.platform()}")
    line((default_dist() / "index.html").exists(), f"web app: {default_dist()}")
    line(True, f"GDAL {rasterio.__gdal_version__}, PROJ {proj_version_str}")
    line(_claude_code() is not None, f"Claude Code: {_claude_code() or 'not found'}")
    settings = load_settings()
    print(f"  settings: {settings.path.parent if settings.path else 'unknown'}")
    for provider in PROVIDERS:
        source = key_source(provider, settings)
        state = f"set up (key from {source})" if source else "set up"
        if not configured(provider, settings):
            state = f"not set up: paste {key_name(provider)} in the app's model menu"
        line(configured(provider, settings), f"{provider.label}: {state}", essential=False)
    if not any(configured(p, settings) for p in PROVIDERS):
        print("  The agent needs one model provider set up; the map works without one.")
    return ok


def _folder(value: str) -> Path:
    folder = Path(value).expanduser().resolve()
    if not folder.is_dir():
        raise LociError(f"{folder} is not a folder.", hint="loci open <folder of your data>")
    return folder


def _free_port(start: int) -> int:
    for port in range(start, start + PORTS_TRIED):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise LociError(f"Ports {start} to {start + PORTS_TRIED - 1} are all in use.")


def _open_when_ready(port: int, url: str, wait: float = 20.0) -> None:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.2)


def _claude_code() -> str | None:
    """The Claude Code the Agent SDK will run: its own bundled copy, else one on the PATH."""
    import shutil

    import claude_agent_sdk

    name = "claude.exe" if sys.platform == "win32" else "claude"
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    return str(bundled) if bundled.exists() else shutil.which("claude")


if __name__ == "__main__":
    main()
