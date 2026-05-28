from pathlib import Path

from c3x.config import CONFIG_PATH, load_config, write_default_config


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
