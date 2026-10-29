"""The models the app can talk to: which provider serves each, and with which credentials (D-048).

Claude models run in Claude Code through the Claude Agent SDK, reached directly through Anthropic
with an API key, or through Amazon Bedrock, Google Vertex AI or Microsoft Foundry. Models of other
providers (OpenAI, Google Gemini, OpenRouter) run in loci's own agent loop on Pydantic AI
(loop.py), with the same tools, reviews and checks.

A model is named "<provider>:<model>", such as "claude:opus" or "openai:gpt-6-astra". Keys and
choices belong to the user, never to the project, and keys never go back to the browser. They
come from, in this order:

- environment variables, such as OPENAI_API_KEY, read when loci starts;
- keys.toml, which the app writes when a key is pasted into the model menu (D-049);
- config.toml, the user's own settings, which loci only reads:

    [claude]
    route = "bedrock"                 # anthropic (default), bedrock, vertex or foundry
    env = { AWS_REGION = "us-east-1" }

    [openai]
    api_key = "sk-..."
    models = ["gpt-6-astra", { id = "gpt-6-luna", label = "GPT-6 Luna", note = "Cheapest" }]

Both files live in the user's settings folder: %APPDATA%\\loci on Windows, ~/.config/loci
elsewhere, or beside $LOCI_CONFIG. Both are read for every message, so changes need no restart.
"""

import json
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from loci.errors import LociError

CONFIG_ENV = "LOCI_CONFIG"
KEYS_FILE = "keys.toml"
Engine = Literal["claude", "loop"]
KeySource = Literal["environment", "app", "config"]

# How Claude Code reaches Claude: the environment each route needs on top of that cloud's own
# credentials, which Claude Code reads as usual.
CLAUDE_ROUTES: dict[str, tuple[str, dict[str, str]]] = {
    "anthropic": ("Anthropic", {}),
    "bedrock": ("Amazon Bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1"}),
    "vertex": ("Google Vertex AI", {"CLAUDE_CODE_USE_VERTEX": "1"}),
    "foundry": ("Microsoft Foundry", {"CLAUDE_CODE_USE_FOUNDRY": "1"}),
}
# Set up outside loci, by the user or their organisation: Claude Code reaches Anthropic through
# them without a key of loci's.
CLAUDE_GATEWAYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
KEY_PATTERN = re.compile(r"[\x21-\x7e]{8,512}")  # printable ASCII without spaces


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    engine: Engine
    vendor: str  # whose API key it takes, as people name it: "an Anthropic API key"
    keys: tuple[str, ...]  # environment variables that can hold its API key, first found wins
    keys_url: str  # where to get a key
    models: tuple[tuple[str, str, str], ...]  # defaults: (model, label, note)


PROVIDERS = (
    Provider(
        "claude",
        "Claude",
        "claude",
        "Anthropic",
        ("ANTHROPIC_API_KEY",),
        "https://platform.claude.com/settings/keys",
        (
            ("opus", "Opus", "Most capable"),
            ("sonnet", "Sonnet", "Fast and capable"),
            ("haiku", "Haiku", "Fastest"),
        ),
    ),
    Provider(
        "openai",
        "OpenAI",
        "loop",
        "OpenAI",
        ("OPENAI_API_KEY",),
        "https://platform.openai.com/api-keys",
        (
            ("gpt-6-astra", "GPT-6 Astra", "Most capable"),
            ("gpt-6.1-terra", "GPT-6.1 Terra", "Fast"),
        ),
    ),
    Provider(
        "google",
        "Google Gemini",
        "loop",
        "Google AI Studio",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "https://aistudio.google.com/apikey",
        (
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Most capable, preview"),
            ("gemini-3.8-flash", "Gemini 3.8 Flash", "Fast"),
        ),
    ),
    Provider(
        "openrouter",
        "OpenRouter",
        "loop",
        "OpenRouter",
        ("OPENROUTER_API_KEY",),
        "https://openrouter.ai/settings/keys",
        (
            ("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "Open weights"),
            ("qwen/qwen3.8-flash", "Qwen 3.8 Flash", "Open weights"),
            ("moonshotai/kimi-k3", "Kimi K3", "Open weights"),
        ),
    ),
)
BY_ID = {p.id: p for p in PROVIDERS}
DEFAULT_MODEL = "claude:opus"


class ModelInfo(BaseModel):
    id: str  # "<provider>:<model>"
    label: str
    note: str


class ProviderInfo(BaseModel):
    """A provider as the model picker shows it; its key itself never leaves the server."""

    id: str
    label: str
    engine: Engine
    configured: bool
    setup: str | None  # how to set it up, when it is not
    route: str | None  # Claude only: how Claude Code reaches it
    vendor: str
    keys_url: str
    key_source: KeySource | None  # where its key comes from, if it has one
    models: list[ModelInfo]


@dataclass(frozen=True)
class Settings:
    """What the environment and the user's settings files say."""

    config: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    path: Path | None = None  # config.toml; keys.toml sits beside it
    keys: Mapping[str, str] = field(default_factory=dict)  # provider id -> key saved by the app


@dataclass(frozen=True)
class Choice:
    """One model, resolved: which engine runs it, with what key or environment."""

    provider: Provider
    model: str
    api_key: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)  # Claude: added to Claude Code's
    route: str | None = None


def config_path(env: Mapping[str, str], *, windows: bool = os.name == "nt") -> Path:
    """$LOCI_CONFIG, else config.toml in loci's folder of the user's settings: %APPDATA%\\loci on
    Windows, $XDG_CONFIG_HOME/loci or ~/.config/loci elsewhere."""
    if value := env.get(CONFIG_ENV):
        return Path(value).expanduser()
    if windows and (appdata := env.get("APPDATA")):
        return Path(appdata) / "loci" / "config.toml"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "loci" / "config.toml"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """The settings now: both files are read on each call, so edits apply without a restart."""
    env = dict(os.environ) if env is None else env
    path = config_path(env)
    config = _read_toml(path, "the loci config file")
    saved = _read_toml(path.with_name(KEYS_FILE), "the keys loci saved")
    keys = {
        name: table["api_key"]
        for name, table in saved.items()
        if isinstance(table, dict) and isinstance(table.get("api_key"), str)
    }
    return Settings(config=config, env=env, path=path, keys=keys)


def providers(settings: Settings) -> list[ProviderInfo]:
    """Every provider, set up or not, with the models to offer for it."""
    infos = []
    for provider in PROVIDERS:
        route = claude_route(settings)[0] if provider.engine == "claude" else None
        ready = configured(provider, settings)
        infos.append(
            ProviderInfo(
                id=provider.id,
                label=provider.label,
                engine=provider.engine,
                configured=ready,
                setup=None if ready else setup_hint(provider),
                route=CLAUDE_ROUTES[route][0] if route else None,
                vendor=provider.vendor,
                keys_url=provider.keys_url,
                key_source=key_source(provider, settings),
                models=_models(provider, settings),
            )
        )
    return infos


def configured(provider: Provider, settings: Settings) -> bool:
    """Whether the provider can answer: it has a key, or Claude is reached another way."""
    if api_key(provider, settings) is not None:
        return True
    if provider.engine != "claude":
        return False
    return claude_route(settings)[0] != "anthropic" or any(
        settings.env.get(name) for name in CLAUDE_GATEWAYS
    )


def resolve(model_id: str, settings: Settings) -> Choice:
    """The provider, model and credentials for "<provider>:<model>"."""
    provider_id, _, model = model_id.partition(":")
    provider = BY_ID.get(provider_id)
    if provider is None or not model:
        raise LociError(
            f"Unknown model {model_id!r}.",
            hint=f"Name it <provider>:<model>, with provider one of {', '.join(BY_ID)}.",
        )
    if not configured(provider, settings):
        raise LociError(f"{provider.label} is not set up.", hint=setup_hint(provider))
    key = api_key(provider, settings)
    if provider.engine == "loop":
        return Choice(provider, model, api_key=key)
    route, env = claude_route(settings)
    if route == "anthropic" and key is not None:
        env = {**env, "ANTHROPIC_API_KEY": key}
    return Choice(provider, model, env=env, route=route)


def api_key(provider: Provider, settings: Settings) -> str | None:
    """The provider's key: from its environment variables, the app's keys, then the config."""
    source = key_source(provider, settings)
    if source == "environment":
        return next(settings.env[name] for name in provider.keys if settings.env.get(name))
    if source == "app":
        return settings.keys[provider.id]
    if source == "config":
        return str(_section(settings, provider.id)["api_key"])
    return None


def key_source(provider: Provider, settings: Settings) -> KeySource | None:
    if any(settings.env.get(name) for name in provider.keys):
        return "environment"
    if settings.keys.get(provider.id):
        return "app"
    value = _section(settings, provider.id).get("api_key")
    return "config" if isinstance(value, str) and value else None


def save_key(provider: Provider, key: str, settings: Settings) -> None:
    """Keep an API key pasted into the app in keys.toml, readable by the user only."""
    key = key.strip()
    if not KEY_PATTERN.fullmatch(key):
        raise LociError(
            "That does not look like an API key.",
            hint="Paste the key itself, without spaces or quotes.",
        )
    _write_keys(settings, {**settings.keys, provider.id: key})


def forget_key(provider: Provider, settings: Settings) -> None:
    """Remove a key the app saved; keys from the environment or config.toml stay where they are."""
    source = key_source(provider, settings)
    if source == "environment":
        names = " or ".join(provider.keys)
        raise LociError(
            f"The {provider.vendor} key comes from {names} in the environment loci started in.",
            hint="Unset it there and start loci again.",
        )
    if source == "config":
        raise LociError(f"The {provider.vendor} key is in {settings.path}; remove it there.")
    _write_keys(settings, {k: v for k, v in settings.keys.items() if k != provider.id})


def claude_route(settings: Settings) -> tuple[str, dict[str, str]]:
    """How Claude Code reaches Claude, and the environment that takes."""
    section = _section(settings, "claude")
    route = section.get("route", "anthropic")
    if route not in CLAUDE_ROUTES:
        raise LociError(
            f"Unknown Claude route {route!r} in {settings.path}.",
            hint=f"Use one of {', '.join(CLAUDE_ROUTES)}.",
        )
    extra = section.get("env", {})
    if not isinstance(extra, dict) or not all(isinstance(v, str) for v in extra.values()):
        raise LociError(f"[claude] env in {settings.path} must map names to strings.")
    return route, {**CLAUDE_ROUTES[route][1], **extra}


def setup_hint(provider: Provider) -> str:
    """How to give a provider its key, in words for the model menu and for errors."""
    names = " or ".join(provider.keys)
    return f"Paste {key_name(provider)} in the model menu, or set {names} before starting loci."


def key_name(provider: Provider) -> str:
    """ "an Anthropic API key", "a Google AI Studio API key"."""
    article = "an" if provider.vendor[0] in "AEIOU" else "a"
    return f"{article} {provider.vendor} API key"


def _models(provider: Provider, settings: Settings) -> list[ModelInfo]:
    """The config file's models for the provider if it lists any, else the defaults."""
    listed = _section(settings, provider.id).get("models")
    if not isinstance(listed, list) or not listed:
        return [
            ModelInfo(id=f"{provider.id}:{m}", label=lb, note=n) for m, lb, n in provider.models
        ]
    models = []
    for entry in listed:
        if isinstance(entry, str):
            models.append(ModelInfo(id=f"{provider.id}:{entry}", label=entry, note=""))
        elif isinstance(entry, dict) and isinstance(entry.get("id"), str):
            name = str(entry["id"])
            label, note = str(entry.get("label", name)), str(entry.get("note", ""))
            models.append(ModelInfo(id=f"{provider.id}:{name}", label=label, note=note))
        else:
            raise LociError(f"Each of [{provider.id}] models in {settings.path} needs an id.")
    return models


def _section(settings: Settings, name: str) -> Mapping[str, Any]:
    section = settings.config.get(name, {})
    if not isinstance(section, dict):
        raise LociError(f"[{name}] in {settings.path} must be a table.")
    return section


def _read_toml(path: Path, what: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise LociError(f"Cannot read {what}, {path}: {e}") from e


def _write_keys(settings: Settings, keys: Mapping[str, str]) -> None:
    """Write keys.toml whole, replacing it in one step; on Windows the user's own settings
    folder is private to them already, elsewhere the file is made so."""
    if settings.path is None:
        raise LociError("loci does not know where your settings folder is.")
    path = settings.path.with_name(KEYS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# API keys pasted into the loci app. loci rewrites this file when they change."]
    for name in sorted(keys):
        lines += ["", f"[{name}]", f"api_key = {json.dumps(keys[name])}"]  # a valid TOML string
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".keys-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
