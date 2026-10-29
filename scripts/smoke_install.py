"""Use an installed loci as a person would: check it, make the demo, open it, load the map.

Run after `uv tool install`, with the `loci` command on the PATH. Only the standard library is
used, so any Python can run it.
"""

import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = 8799


def main() -> None:
    folder = Path(tempfile.mkdtemp()) / "loci-demo"
    subprocess.run(["loci", "--version"], check=True)
    subprocess.run(["loci", "doctor"], check=True)
    subprocess.run(["loci", "demo", str(folder), "--no-open"], check=True)
    server = subprocess.Popen(["loci", "open", str(folder), "--no-browser", "--port", str(PORT)])
    try:
        project = json.loads(_get("/api/project", wait=60))
        page = _get("/")
        models = json.loads(_get("/api/models"))
    finally:
        server.terminate()
        server.wait(timeout=30)
    assert Path(project["project"]).resolve() == folder.resolve(), project
    assert project["suggestions"], "the demo should offer its questions"
    assert '<div id="root">' in page, "the web app should be served at /"
    assert {p["id"] for p in models["providers"]} >= {"claude", "openai"}, models
    print(f"loci served the demo in {folder}, its web app and its models.")


def _get(path: str, wait: float = 0) -> str:
    deadline = time.monotonic() + wait
    while True:
        try:
            url = f"http://127.0.0.1:{PORT}{path}"
            with urllib.request.urlopen(url, timeout=10) as response:
                return str(response.read().decode("utf-8"))
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


if __name__ == "__main__":
    main()
