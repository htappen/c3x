from pathlib import Path

from c3x.config import CONFIG_PATH, load_config, write_default_config


def test_write_default_config_creates_flow_config(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)

    assert path == tmp_path / CONFIG_PATH
    assert path.exists()


def test_load_config_uses_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.agents.codex_command == "codex"
    assert config.limits.max_parallel_workers == 3

