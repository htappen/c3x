from pathlib import Path

from c3x.agent import _agent_command
from c3x.config import C3xConfig


def test_agent_command_substitutes_runtime_paths(tmp_path: Path) -> None:
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["--model", "{model}", "--worktree", "{worktree}", "{prompt}", "{result}"],
            }
        }
    )

    command = _agent_command(
        config,
        tmp_path / "wt",
        tmp_path / "prompt.md",
        tmp_path / "result.json",
        tmp_path / "last.md",
    )

    assert command == [
        "fake-codex",
        "--model",
        "gpt-5.4-mini",
        "--worktree",
        str(tmp_path / "wt"),
        str(tmp_path / "prompt.md"),
        str(tmp_path / "result.json"),
    ]

