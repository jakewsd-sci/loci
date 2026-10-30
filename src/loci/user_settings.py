"""Where the user's own loci settings live: outside every project, so keys never enter one (D-049).

loci's folder of the user's settings holds config.toml, which the user writes, and keys.toml,
which the app writes when a key is pasted into it (see loci.web.providers).
"""

import os
from collections.abc import Mapping
from pathlib import Path

CONFIG_ENV = "LOCI_CONFIG"
KEYS_FILE = "keys.toml"


def config_path(env: Mapping[str, str], *, windows: bool = os.name == "nt") -> Path:
    """$LOCI_CONFIG, else config.toml in loci's folder of the user's settings: %APPDATA%\\loci on
    Windows, $XDG_CONFIG_HOME/loci or ~/.config/loci elsewhere."""
    if value := env.get(CONFIG_ENV):
        return Path(value).expanduser()
    if windows and (appdata := env.get("APPDATA")):
        return Path(appdata) / "loci" / "config.toml"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "loci" / "config.toml"


def keys_path(env: Mapping[str, str]) -> Path:
    """keys.toml, beside config.toml."""
    return config_path(env).with_name(KEYS_FILE)
