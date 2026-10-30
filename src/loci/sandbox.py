"""How run starts a script: inside a sandbox where the computer has one, and always guarded (D-052).

- **guard**: loci's guard (loci.guard) refuses, from inside the script's Python, any write
  outside the run folder, reading keys and credentials, starting programs, and network
  connections other than to the hosts of the declared inputs. It works everywhere, Windows
  included, but cannot see C libraries.
- **os**: where Anthropic's sandbox runtime (`srt`) is installed, on macOS, Linux or WSL2, the
  script also runs inside it, so the operating system enforces the same limits on everything the
  process does, GDAL included.
- **off**: neither, for a script that must reach other places; LOCI_SANDBOX=off asks for it.

LOCI_SANDBOX picks: "auto" (the default: os where srt is installed, else guard), "guard" or
"off". A script reaches what its declared inputs need, and no more: their hosts, and the
credentials their readers use (see reach).
"""

import json
import os
import shutil
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from loci.errors import LociError
from loci.guard import HOSTS_ENV, PRIVATE_ENV, ROOT_ENV, TEMPORARY
from loci.user_settings import config_path, keys_path

MODE_ENV = "LOCI_SANDBOX"
SETTINGS_FILE = ".sandbox.json"
Mode = Literal["os", "guard", "off"]
# Credentials each kind of input is read with; kept from scripts whose inputs do not need them.
CREDENTIALS = {
    "http": ["~/.netrc", "~/_netrc"],  # curl's, which GDAL uses for http(s)
    "s3": ["~/.aws"],
    "gs": ["~/.config/gcloud"],
    "az": ["~/.azure"],
}
# Kept from every script, even for reading, by the guard and by srt; and loci's settings files
# wherever they are (see private).
PRIVATE = ["~/.ssh", "~/.config/loci"]
# GDAL's network paths, and the URL scheme each stands for ("" when a URL follows).
VSI = {
    "/vsicurl/": "",
    "/vsicurl_streaming/": "",
    "/vsis3/": "s3://",
    "/vsis3_streaming/": "s3://",
    "/vsigs/": "gs://",
    "/vsigs_streaming/": "gs://",
    "/vsiaz/": "az://",
    "/vsiaz_streaming/": "az://",
}


@dataclass(frozen=True)
class Plan:
    """The command that runs a script, and the environment it needs on top of run's."""

    mode: Mode
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    without_srt: str | None = None  # why srt, though installed, was not used

    @property
    def note(self) -> str:
        text = {
            "os": "Ran in the operating system's sandbox (srt) with loci's guard: writes only in "
            "its run folder, network only to its inputs' hosts.",
            "guard": "Ran with loci's guard: writes only in its run folder, no programs started, "
            "network only to its inputs' hosts.",
            "off": "Ran without a sandbox (LOCI_SANDBOX=off): it could write and reach anywhere.",
        }[self.mode]
        return f"{text} srt was not used: {self.without_srt}." if self.without_srt else text


@dataclass(frozen=True)
class Reach:
    """What a script's inputs need: the hosts their readers connect to, and the credentials
    they read."""

    hosts: list[str] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)


def plan(
    script: str, run_dir: Path, inputs: Iterable[str], env: Mapping[str, str] | None = None
) -> Plan:
    """How to run `script` in `run_dir`, reaching only what reading `inputs` (URIs) needs."""
    env = os.environ if env is None else env
    choice = env.get(MODE_ENV, "auto").strip().lower()
    if choice not in ("auto", "guard", "off"):
        raise LociError(f"{MODE_ENV}={choice!r} is not one of auto, guard or off.")
    if choice == "off":
        return Plan("off", [sys.executable, script])
    needs = reach(inputs, env)
    guarded = {
        ROOT_ENV: str(run_dir),
        HOSTS_ENV: ",".join(needs.hosts),
        PRIVATE_ENV: os.pathsep.join(private(env, needs)),
    }
    command = [sys.executable, "-m", "loci.guard", script]
    seed_matplotlib(run_dir / TEMPORARY / "matplotlib")
    runtime = os_sandbox() if choice == "auto" else None
    if runtime is None:
        return Plan("guard", command, guarded)
    if bare := [h for h in needs.hosts if "." not in h and h != "localhost" and ":" not in h]:
        # srt allows domains only: a host on the local network, such as "fileserver", is not one.
        names = ", ".join(bare)
        return Plan("guard", command, guarded, f"it cannot allow {names}, a host without a domain")
    settings = run_dir / SETTINGS_FILE
    settings.write_text(json.dumps(srt_settings(run_dir, needs, env), indent=2), encoding="utf-8")
    return Plan("os", [runtime, "--settings", str(settings), "--", *command], guarded)


def seed_matplotlib(folder: Path) -> None:
    """Give a run matplotlib's list of fonts. Its own folder starts empty, being in the run
    folder, and building the list there again in every run is slow where many fonts are
    installed, as on Windows. The list is built once, in the user's own cache."""
    import matplotlib
    from matplotlib import font_manager

    name = f"fontlist-v{font_manager.FontManager.__version__}.json"
    built = Path(matplotlib.get_cachedir(), name)
    if built.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built, folder / name)


def os_sandbox() -> str | None:
    """Anthropic's sandbox runtime, where it can be used: installed, and not on Windows, where it
    is still an alpha that needs an administrator to install."""
    if sys.platform == "win32":
        return None
    return shutil.which("srt")


def srt_settings(run_dir: Path, needs: Reach, env: Mapping[str, str]) -> dict[str, object]:
    """srt's settings for one run: write in the run folder, reach the inputs' hosts only."""
    # srt asks for every list, empty or not, so a file can never fall back to its defaults.
    domains = [f"[{h}]" if ":" in h else h for h in needs.hosts]  # IPv6 addresses in brackets
    return {
        "network": {"allowedDomains": domains, "deniedDomains": []},
        "filesystem": {
            "allowWrite": [str(run_dir)],
            "denyWrite": [],
            "denyRead": private(env, needs),
        },
    }


def private(env: Mapping[str, str], needs: Reach) -> list[str]:
    """What a script may not read: PRIVATE, the credentials its inputs do not need, and loci's
    settings and keys files wherever they are ($LOCI_CONFIG, %APPDATA%, $XDG_CONFIG_HOME): files,
    not their folder, which may hold data."""
    unneeded = [c for kind in CREDENTIALS.values() for c in kind if c not in needs.credentials]
    return list(dict.fromkeys([*PRIVATE, *unneeded, str(config_path(env)), str(keys_path(env))]))


def reach(uris: Iterable[str], env: Mapping[str, str]) -> Reach:
    """The hosts that reading `uris` connects to, and the credentials it reads; local files need
    neither. Cloud storage is reached at its usual endpoints: <bucket>.s3.amazonaws.com (and
    in $AWS_REGION, or at $AWS_S3_ENDPOINT), storage.googleapis.com, and the account in
    $AZURE_STORAGE_ACCOUNT."""
    hosts: set[str] = set()
    credentials: set[str] = set()
    for uri in uris:
        url = urlparse(_url(uri))
        scheme = "http" if url.scheme in ("http", "https") else url.scheme
        if scheme == "http":
            hosts.add(url.hostname or "")
        elif scheme == "s3":
            regions = {env.get("AWS_REGION"), env.get("AWS_DEFAULT_REGION")} - {None, ""}
            hosts |= {f"{url.netloc}.s3.amazonaws.com"}
            hosts |= {f"{url.netloc}.s3.{region}.amazonaws.com" for region in regions}
            if endpoint := env.get("AWS_S3_ENDPOINT"):
                hosts.add(
                    urlparse(endpoint if "//" in endpoint else f"//{endpoint}").hostname or ""
                )
        elif scheme == "gs":
            hosts |= {"storage.googleapis.com", "oauth2.googleapis.com"}
        elif scheme == "az" and (account := env.get("AZURE_STORAGE_ACCOUNT")):
            hosts.add(f"{account}.blob.core.windows.net")
        credentials.update(CREDENTIALS.get(scheme, []))
    return Reach(sorted(h.lower() for h in hosts - {""}), sorted(credentials))


def _url(uri: str) -> str:
    """The URL a GDAL path such as /vsizip//vsicurl/https://... reads, or the URI itself."""
    found = [(uri.find(prefix), prefix) for prefix in VSI if prefix in uri]
    if not found:
        return uri
    at, prefix = min(found)
    return VSI[prefix] + uri[at + len(prefix) :]
