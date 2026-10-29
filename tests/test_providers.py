"""Which providers and models the app offers, from the environment and the user's config file."""

from pathlib import Path

import pytest

from loci.errors import LociError
from loci.web.providers import (
    DEFAULT_MODEL,
    Settings,
    config_path,
    load_settings,
    providers,
    resolve,
)


def _settings(tmp_path: Path, config: str = "", **env: str) -> Settings:
    path = tmp_path / "config.toml"
    if config:
        path.write_text(config)
    return load_settings({"LOCI_CONFIG": str(path), **env})


def test_claude_is_always_offered_and_other_providers_once_they_have_a_key(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, OPENAI_API_KEY="sk-test")

    listed = {p.id: p for p in providers(settings)}

    assert list(listed) == ["claude", "openai", "google", "openrouter"]
    assert listed["claude"].configured and listed["claude"].route == "Anthropic"
    assert listed["openai"].configured and listed["openai"].setup is None
    assert not listed["google"].configured
    assert listed["google"].setup is not None and "GEMINI_API_KEY" in listed["google"].setup
    assert [m.id for m in listed["claude"].models] == [
        "claude:opus",
        "claude:sonnet",
        "claude:haiku",
    ]
    assert all(m.id.startswith("openrouter:") for m in listed["openrouter"].models)
    assert DEFAULT_MODEL == "claude:opus"


def test_models_resolve_to_their_engine_and_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path, GEMINI_API_KEY="g-env")

    claude = resolve("claude:sonnet", settings)
    gemini = resolve("google:gemini-3.8-flash", settings)
    routed = resolve("openrouter:qwen/qwen3.8-flash", _settings(tmp_path, OPENROUTER_API_KEY="or"))

    assert (claude.provider.engine, claude.model, claude.route) == ("claude", "sonnet", "anthropic")
    assert (gemini.provider.engine, gemini.model, gemini.api_key) == (
        "loop",
        "gemini-3.8-flash",
        "g-env",
    )
    assert routed.model == "qwen/qwen3.8-flash"  # the model keeps its own slash


def test_unknown_or_unset_providers_are_errors_with_hints(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(LociError, match="Unknown model") as unknown:
        resolve("mistral:large", settings)
    with pytest.raises(LociError, match="Unknown model 'opus'"):
        resolve("opus", settings)  # a model is always named with its provider
    with pytest.raises(LociError, match="OpenAI is not set up") as unset:
        resolve("openai:gpt-6-astra", settings)

    assert unknown.value.hint is not None and "openrouter" in unknown.value.hint
    assert unset.value.hint is not None and "OPENAI_API_KEY" in unset.value.hint


def test_the_config_file_sets_keys_models_and_the_claude_route(tmp_path: Path) -> None:
    config = """
[claude]
route = "bedrock"
env = { AWS_REGION = "ap-southeast-2" }

[openai]
api_key = "sk-file"
models = ["gpt-6-luna", { id = "gpt-6-astra", label = "Astra", note = "Best" }]
"""
    settings = _settings(tmp_path, config, OPENAI_API_KEY="sk-env")

    listed = {p.id: p for p in providers(settings)}
    claude = resolve("claude:opus", settings)
    openai = resolve("openai:gpt-6-luna", settings)

    assert listed["claude"].route == "Amazon Bedrock"
    assert dict(claude.env) == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "ap-southeast-2"}
    assert [(m.id, m.label, m.note) for m in listed["openai"].models] == [
        ("openai:gpt-6-luna", "gpt-6-luna", ""),
        ("openai:gpt-6-astra", "Astra", "Best"),
    ]
    assert openai.api_key == "sk-env"  # the environment wins over the file
    assert resolve("openai:x", _settings(tmp_path, config)).api_key == "sk-file"


def test_a_broken_config_file_is_an_error_naming_it(tmp_path: Path) -> None:
    bad_route = _settings(tmp_path, '[claude]\nroute = "pigeon"\n')

    with pytest.raises(LociError, match="Unknown Claude route 'pigeon'"):
        resolve("claude:opus", bad_route)
    (tmp_path / "config.toml").write_text("[openai\n")
    with pytest.raises(LociError, match="Cannot read the loci config file"):
        load_settings({"LOCI_CONFIG": str(tmp_path / "config.toml")})


def test_the_config_file_lives_in_the_users_config_folder(tmp_path: Path) -> None:
    assert config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "loci" / "config.toml"
    assert config_path({"LOCI_CONFIG": "~/x.toml"}) == Path.home() / "x.toml"
