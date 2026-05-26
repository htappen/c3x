from pathlib import Path

from c3x import agent
from c3x.agent import _agent_command
from c3x.agent import start_worker
from c3x.agent import _worker_prompt
from c3x.config import C3xConfig
from c3x.beads import BeadSummary


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


def test_agent_command_can_resume_session(tmp_path: Path) -> None:
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_resume_args": ["exec", "resume", "{session_id}", "{prompt}"],
            }
        }
    )

    command = _agent_command(
        config,
        tmp_path / "wt",
        tmp_path / "prompt.md",
        tmp_path / "result.json",
        tmp_path / "last.md",
        resume_session_id="019e61af-8603-7b53-8099-9284e6bc16bd",
    )

    assert command == [
        "fake-codex",
        "exec",
        "resume",
        "019e61af-8603-7b53-8099-9284e6bc16bd",
        str(tmp_path / "prompt.md"),
    ]


def test_worker_prompt_includes_caveman_mode(tmp_path: Path) -> None:
    prompt = _worker_prompt(
        BeadSummary(id="bd-1", title="Fix auth"),
        tmp_path / "result.json",
    )

    assert "CAVEMAN MODE ACTIVE" in prompt
    assert "Task: bd-1" in prompt


def test_worker_prompt_forbids_beads_and_git_landing(tmp_path: Path) -> None:
    prompt = _worker_prompt(
        BeadSummary(id="bd-1", title="Fix auth"),
        tmp_path / "result.json",
    )

    assert "Do not run Beads commands" in prompt
    assert "Do not run `git commit`, `git push`, `git pull`, `git merge`" in prompt
    assert "The supervisor will commit and merge" in prompt


def test_start_worker_launches_in_new_process_session(monkeypatch, tmp_path: Path) -> None:
    popen_kwargs: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        popen_kwargs.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(agent, "create_worktree", lambda root, branch, worktree: worktree.mkdir(parents=True))
    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}"],
            }
        }
    )

    record = start_worker(tmp_path, config, BeadSummary(id="bd-1", title="Fix auth"))

    assert record.pid == 12345
    assert popen_kwargs["start_new_session"] is True
