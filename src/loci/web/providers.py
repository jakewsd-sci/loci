"""The models the app can talk to: which provider serves each, and with which credentials (D-048).

Claude models run in Claude Code through the Claude Agent SDK, reached directly through Anthropic
or through Amazon Bedrock, Google Vertex AI or Microsoft Foundry. Models of other providers
(OpenAI, Google Gemini, OpenRouter) run in loci's own agent loop on Pydantic AI (loop.py), with
the same tools, reviews and checks.

A model is named "<provider>:<model>", such as "claude:opus" or "openai:gpt-6-astra". Keys and
choices come from environment variables or the user's config file ($LOCI_CONFIG, else
~/.config/loci/config.toml), never from the project or the browser:

    [claude]
    route = "bedrock"                 # anthropic (default), bedrock, vertex or foundry
    env = { AWS_REGION = "us-east-1" }

    [openai]
    api_key = "sk-..."                # or set OPENAI_API_KEY
    models = ["gpt-6-astra", { id = "gpt-6-luna", label = "GPT-6 Luna", note = "Cheapest" }]
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from loci.errors import LociError

CONFIG_ENV = "LOCI_CONFIG"
Engine = Literal["claude", "loop"]

# How Claude Code reaches Claude: the environment each route needs on top of that cloud's own
# credentials, which Claude Code reads as usual.
CLAUDE_ROUTES: dict[str, tuple[str, dict[str, str]]] = {
    "anthropic": ("Anthropic", {}),
    "bedrock": ("Amazon Bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1"}),
    "vertex": ("Google Vertex AI", {"CLAUDE_CODE_USE_VERTEX": "1"}),
    "foundry": ("Microsoft Foundry", {"CLAUDE_CODE_USE_FOUNDRY": "1"}),
}


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    engine: Engine
    keys: tuple[str, ...]  # environment variables that can hold its API key, first found wins
    models: tuple[tuple[str, str, str], ...]  # defaults: (model, label, note)


PROVIDERS = (
    Provider(
        "claude",
        "Claude",
        "claude",
        (),
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
        ("OPENAI_API_KEY",),
        (
            ("gpt-6-astra", "GPT-6 Astra", "Most capable"),
            ("gpt-6.1-terra", "GPT-6.1 Terra", "Fast"),
        ),
    ),
    Provider(
        "google",
        "Google Gemini",
        "loop",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        (
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Most capable, preview"),
            ("gemini-3.8-flash", "Gemini 3.8 Flash", "Fast"),
        ),
    ),
    Provider(
        "openrouter",
        "OpenRouter",
        "loop",
        ("OPENROUTER_API_KEY",),
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
    """A provider as the model picker shows it."""

    id: str
    label: str
    engine: Engine
    configured: bool
    setup: str | None  # how to set it up, when it is not
    route: str | None  # Claude only: how Claude Code reaches it
    models: list[ModelInfo]


@dataclass(frozen=True)
class Settings:
    """What the user's config file and the environment say."""

    config: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    path: Path | None = None


@dataclass(frozen=True)
class Choice:
    """One model, resolved: which engine runs it, with what key or environment."""

    provider: Provider
    model: str
    api_key: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)  # Claude: added to Claude Code's
    route: str | None = None


def config_path(env: Mapping[str, str]) -> Path:
    """$LOCI_CONFIG, else config.toml in $XDG_CONFIG_HOME/loci or ~/.config/loci."""
    if value := env.get(CONFIG_ENV):
        return Path(value).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "loci" / "config.toml"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """The settings now: the config file is read on each call, so edits apply without a restart."""
    env = dict(os.environ) if env is None else env
    path = config_path(env)
    if not path.exists():
        return Settings(env=env, path=path)
    try:
        config = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise LociError(f"Cannot read the loci config file {path}: {e}") from e
    return Settings(config=config, env=env, path=path)


def providers(settings: Settings) -> list[ProviderInfo]:
    """Every provider, set up or not, with the models to offer for it."""
    infos = []
    for provider in PROVIDERS:
        route = claude_route(settings)[0] if provider.engine == "claude" else None
        configured = provider.engine == "claude" or api_key(provider, settings) is not None
        infos.append(
            ProviderInfo(
                id=provider.id,
                label=provider.label,
                engine=provider.engine,
                configured=configured,
                setup=None if configured else _setup(provider, settings),
                route=CLAUDE_ROUTES[route][0] if route else None,
                models=_models(provider, settings),
            )
        )
    return infos


def resolve(model_id: str, settings: Settings) -> Choice:
    """The provider, model and credentials for "<provider>:<model>"; a bare name means Claude."""
    provider_id, _, model = model_id.partition(":") if ":" in model_id else ("claude", "", model_id)
    provider = BY_ID.get(provider_id)
    if provider is None or not model:
        raise LociError(
            f"Unknown model {model_id!r}.",
            hint=f"Name it <provider>:<model>, with provider one of {', '.join(BY_ID)}.",
        )
    if provider.engine == "claude":
        route, env = claude_route(settings)
        return Choice(provider, model, env=env, route=route)
    key = api_key(provider, settings)
    if key is None:
        raise LociError(f"{provider.label} is not set up.", hint=_setup(provider, settings))
    return Choice(provider, model, api_key=key)


def api_key(provider: Provider, settings: Settings) -> str | None:
    """The provider's key: from its environment variables first, then the config file."""
    for name in provider.keys:
        if value := settings.env.get(name):
            return value
    value = _section(settings, provider.id).get("api_key")
    return value if isinstance(value, str) and value else None


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


def _setup(provider: Provider, settings: Settings) -> str:
    keys = " or ".join(provider.keys)
    return f"Set {keys}, or add api_key under [{provider.id}] in {settings.path}."
