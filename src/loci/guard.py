"""loci's guard: run a script so it cannot reach beyond its run folder (D-052).

    python -m loci.guard script.py

Started by run (see loci.sandbox), before the script's first line. Python's audit hooks refuse:

- writing, moving or deleting files outside the run folder (LOCI_GUARD_ROOT);
- reading keys and credentials, loci's own among them (LOCI_GUARD_PRIVATE);
- starting other programs;
- network connections, except to the hosts of the run's declared inputs (LOCI_GUARD_HOSTS),
  through any proxy the computer uses.

A refusal is a PermissionError naming what was stopped. The guard catches mistakes, not a
determined attack: code in C libraries, such as GDAL's own file and network access, is not seen
by audit hooks. Where it is installed, Anthropic's sandbox runtime adds limits the operating
system enforces on everything.
"""

import os
import re
import runpy
import socket
import sys
import tempfile
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

ROOT_ENV = "LOCI_GUARD_ROOT"
HOSTS_ENV = "LOCI_GUARD_HOSTS"
PRIVATE_ENV = "LOCI_GUARD_PRIVATE"  # files and folders not to read, separated by os.pathsep
TEMPORARY = ".tmp"  # the script's temporary folder, inside its run folder
PROXY_ENVS = ("https_proxy", "http_proxy", "all_proxy", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
PROCESS_EVENTS = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.startfile",
    "os.fork",
    "os.forkpty",
    "pty.spawn",
}
# Events that change files, and which of their arguments are paths.
PATH_EVENTS = {
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "os.link": (0, 1),
    "os.symlink": (1,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
}
NULL_DEVICES = {os.path.normcase(os.devnull), "nul"}
# The host a request sent through a proxy asks for: CONNECT host:port, or GET http://host/...
PROXIED = re.compile(rb"(?:CONNECT ([^\s:/]+)|[A-Z]+ https?://([^\s:/?#]+))", re.IGNORECASE)
WHY = (
    "scripts write only in their run folder (save results with loci.script.save), read no keys "
    "or credentials, start no programs, and reach the network only for their declared inputs"
)


class Guard:
    """The rules for one run: where it may write, what it may not read, which hosts it may reach."""

    def __init__(self, root: str, hosts: Iterable[str], private: Iterable[str] = ()) -> None:
        self.root = _full(root)
        self.private = [_full(os.path.expanduser(p)) for p in private if p]
        self.proxies = {
            (urlparse(os.environ[name]).hostname or "").lower()
            for name in PROXY_ENVS
            if os.environ.get(name)
        } - {""}
        self.named = {h.lower() for h in hosts if h} - {""}
        # The inputs are reached through the computer's proxy, if it uses one.
        self.hosts = self.named | self.proxies if self.named else set()
        self.addresses: set[str] = set()  # resolved from allowed hosts

    def check(self, event: str, args: tuple[Any, ...]) -> None:
        """The audit hook: return quietly, or raise PermissionError saying what was stopped."""
        if event == "open":
            path, mode, flags = args
            if self.hidden(path):
                self.refuse(f"reading {path}")
            if _writes(mode, flags) and not self.inside(path):
                self.refuse(f"writing {path}")
        elif event in PATH_EVENTS:
            for i in PATH_EVENTS[event]:
                if i < len(args) and not self.inside(args[i]):
                    self.refuse(f"changing {args[i]} ({event})")
        elif event in PROCESS_EVENTS:
            self.refuse(f"starting a program ({event})")
        elif event in ("socket.getaddrinfo", "socket.gethostbyname"):
            host = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
            if host.lower() not in self.hosts and host not in self.addresses:
                via = " (the network's proxy)" if host.lower() in self.proxies else ""
                self.refuse(f"reaching {host}{via}")
        elif event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
            address = args[1] if len(args) > 1 else None
            ip = address[0] if isinstance(address, tuple) and address else address
            if (
                ip is not None
                and str(ip) not in self.addresses
                and str(ip).lower() not in self.hosts
            ):
                self.refuse(f"connecting to {ip}")
        elif event == "http.client.send" and self.proxies:
            # Through a proxy, http.client (under requests and urllib) names the host it wants;
            # other clients' requests to the proxy are not seen.
            asked = PROXIED.match(args[1]) if isinstance(args[1], bytes | bytearray) else None
            target = (asked.group(1) or asked.group(2)).decode().lower() if asked else None
            if target is not None and target not in self.named:
                self.refuse(f"reaching {target} through the network's proxy")

    def inside(self, path: Any) -> bool:
        """Whether a path is in the run folder; file descriptors and the null device are fine."""
        if isinstance(path, int) or path is None:
            return True
        name = _name(path)
        full = _full(name)
        if full in NULL_DEVICES or os.path.normcase(name) in NULL_DEVICES:
            return True
        return _within(full, self.root)

    def hidden(self, path: Any) -> bool:
        """Whether a path is one of the private files, or in one of the private folders."""
        if isinstance(path, int) or path is None or not self.private:
            return False
        full = _full(_name(path))
        return any(_within(full, place) for place in self.private)

    def refuse(self, what: str) -> None:
        raise PermissionError(f"loci's guard stopped {what}: {WHY}.")

    def resolver(self, real: Callable[..., Any]) -> Callable[..., Any]:
        """socket.getaddrinfo, noting the addresses of allowed hosts so they can be reached."""

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            found = real(host, *args, **kwargs)
            name = host.decode() if isinstance(host, bytes) else str(host)
            if name.lower() in self.hosts:
                self.addresses.update(str(entry[4][0]) for entry in found)
            return found

        return getaddrinfo


def install(guard: Guard) -> None:
    """Install the guard for the rest of this process; it cannot be removed."""
    socket.getaddrinfo = guard.resolver(socket.getaddrinfo)
    sys.addaudithook(guard.check)


def _name(path: Any) -> str:
    return os.fsdecode(path) if isinstance(path, bytes | os.PathLike) else str(path)


def _full(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _within(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # another drive, on Windows
        return False


def _writes(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str):
        return any(c in mode for c in "wax+")
    return isinstance(flags, int) and flags >= 0 and bool(flags & WRITE_FLAGS)


def keep_temporary_files_in(root: str) -> None:
    """Send temporary files, matplotlib's cache among them, to the run folder, the one place the
    script may write. Set here, inside any sandbox, because srt keeps its own sockets in the
    temporary folder it is given, and a run folder can be too deep a path for a socket."""
    folder = os.path.join(root, TEMPORARY)
    os.makedirs(folder, exist_ok=True)
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[name] = folder
    os.environ["MPLCONFIGDIR"] = os.path.join(folder, "matplotlib")
    tempfile.tempdir = folder
    sys.dont_write_bytecode = True  # imports would otherwise try to write beside the modules


def main() -> None:
    script = sys.argv[1]
    root = os.environ[ROOT_ENV]
    hosts = os.environ.get(HOSTS_ENV, "").split(",")
    private = os.environ.get(PRIVATE_ENV, "").split(os.pathsep)
    keep_temporary_files_in(root)
    install(Guard(root, hosts, private))
    sys.argv = sys.argv[1:]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
