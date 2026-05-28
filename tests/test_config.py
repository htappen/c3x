from pathlib import Path

import pytest

from c3x.config import (
    CONFIG_PATH,
    ProviderModelConfig,
    load_config,
    migrate_config_data,
    migrate_flat_models,
    write_default_config,
)


def test_write_default_config_creates_flow_config(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)

    assert path == tmp_path / CONFIG_PATH
    assert path.exists()


def test_load_config_uses_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.agents.provider == "codex"
    assert config.agents.provider_overrides == {}
    assert config.agents.codex_command == "codex"
    assert "resume" in config.agents.codex_resume_args
    assert config.agents.antigravity_command == "~/.local/bin/agy.va39"
    assert "--print" in config.agents.antigravity_args
    assert "--conversation" in config.agents.antigravity_resume_args
    assert config.limits.max_parallel_workers == 3


def test_load_config_supports_provider_overrides(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        """
agents:
  provider: codex
  provider_overrides:
    worker: antigravity
    conflict_resolver: codex
""",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.agents.provider == "codex"
    assert config.agents.provider_overrides == {
        "worker": "antigravity",
        "conflict_resolver": "codex",
    }


# ---------------------------------------------------------------------------
# Per-provider ModelConfig tests
# ---------------------------------------------------------------------------


def test_default_config_has_per_provider_models(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert "codex" in config.models.root
    assert "agy" in config.models.root
    assert isinstance(config.models["codex"], ProviderModelConfig)


def test_models_for_provider_returns_correct_block(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        """
models:
  codex:
    worker: o4-mini
    architect: o4
    reviewer: o4
    critic: o4
    verify: o4
  agy:
    worker: claude-sonnet
    architect: claude-opus
    reviewer: claude-sonnet
    critic: claude-sonnet
    verify: claude-sonnet
""",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.models_for_provider("codex").worker == "o4-mini"
    assert config.models_for_provider("agy").worker == "claude-sonnet"


def test_models_for_provider_falls_back_to_codex_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    # An unknown provider should fall back to the codex block.
    codex_worker = config.models_for_provider("codex").worker
    assert config.models_for_provider("unknown_provider").worker == codex_worker


def test_models_for_provider_falls_back_to_defaults_when_no_codex_block(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        """
models:
  agy:
    worker: claude-sonnet
    architect: claude-opus
    reviewer: claude-sonnet
    critic: claude-sonnet
    verify: claude-sonnet
""",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    # No codex block → falls back to ProviderModelConfig defaults.
    fallback = config.models_for_provider("unknown_provider")
    assert isinstance(fallback, ProviderModelConfig)


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


def test_migrate_flat_models_produces_per_provider_dict() -> None:
    flat = {
        "architect": "gpt-5.4",
        "worker": "gpt-5.4-mini",
        "reviewer": "gpt-5.4",
        "critic": "gpt-5.4",
        "verify": "gpt-5.4",
    }

    result = migrate_flat_models(flat)

    assert "codex" in result
    assert "agy" in result
    assert result["codex"]["worker"] == "gpt-5.4-mini"
    assert result["agy"]["worker"] == "gpt-5.4-mini"


def test_migrate_config_data_migrates_flat_models_section() -> None:
    data = {
        "models": {
            "architect": "gpt-5.4",
            "worker": "gpt-5.4-mini",
            "reviewer": "gpt-5.4",
            "critic": "gpt-5.4",
            "verify": "gpt-5.4",
        }
    }

    result = migrate_config_data(data)

    assert "codex" in result["models"]
    assert result["models"]["codex"]["worker"] == "gpt-5.4-mini"


def test_migrate_config_data_does_not_alter_new_format() -> None:
    data = {
        "models": {
            "codex": {"worker": "o4-mini", "architect": "o4", "reviewer": "o4", "critic": "o4", "verify": "o4"},
            "agy": {
                "worker": "claude-sonnet",
                "architect": "claude-opus",
                "reviewer": "claude-sonnet",
                "critic": "claude-sonnet",
                "verify": "claude-sonnet",
            },
        }
    }

    result = migrate_config_data(data)

    assert result == data


def test_migrate_config_data_does_not_mutate_original() -> None:
    original: dict = {
        "models": {
            "architect": "gpt-5.4",
            "worker": "gpt-5.4-mini",
            "reviewer": "gpt-5.4",
            "critic": "gpt-5.4",
            "verify": "gpt-5.4",
        }
    }
    original_id = id(original)

    result = migrate_config_data(original)

    assert id(result) != original_id
    assert "architect" in original["models"], "original dict must not be mutated"


def test_load_config_migrates_legacy_flat_models_on_read(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        """
models:
  architect: gpt-5.4
  worker: gpt-5.4-mini
  reviewer: gpt-5.4
  critic: gpt-5.4
  verify: gpt-5.4
""",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.models_for_provider("codex").worker == "gpt-5.4-mini"
    assert config.models_for_provider("agy").worker == "gpt-5.4-mini"
