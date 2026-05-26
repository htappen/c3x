from pathlib import Path

from c3x import agent
from c3x.agent import _agent_command
from c3x.agent import start_worker
from c3x.agent import _worker_prompt
from c3x.schema import RunRecord
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


def test_worker_prompt_starts_with_status_probe(tmp_path: Path) -> None:
    prompt = _worker_prompt(
        BeadSummary(id="bd-1", title="Fix auth"),
        tmp_path / "result.json",
    )

    assert prompt.startswith("/status\n\n")
    assert "preserve the `/status` output in the worker log/transcript" in prompt


def test_retry_and_conflict_prompts_start_with_status_probe(tmp_path: Path) -> None:
    previous = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1",
        worktree=str(tmp_path / "wt"),
        prompt=str(tmp_path / "prompt.md"),
        result=str(tmp_path / "result.json"),
        last_message=str(tmp_path / "last.md"),
        attempt=2,
    )
    task = BeadSummary(id="bd-1", title="Fix auth")

    prompts = [
        agent._resume_worker_prompt(task, tmp_path / "result.json", previous=previous, reason="retry"),
        agent._continue_worktree_prompt(task, tmp_path / "result.json", previous=previous, reason="retry"),
        agent._conflict_resolver_prompt(
            task,
            tmp_path / "result.json",
            source_branch="c3x/bd-1",
            target_branch="main",
            target_revision="HEAD",
            conflicted_files=["app.py"],
            original_result="{}",
        ),
    ]

    assert all(prompt.startswith("/status\n\n") for prompt in prompts)


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


def test_agent_command_substitutes_runtime_paths_antigravity(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Task: bd-1\nHello prompt content", encoding="utf-8")
    
    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "antigravity",
                "antigravity_command": "fake-agy",
                "antigravity_args": [
                    "--dangerously-skip-permissions",
                    "--sandbox",
                    "--add-dir",
                    "{worktree}",
                    "--print",
                    "{prompt_content}",
                ],
            }
        }
    )

    command = _agent_command(
        config,
        tmp_path / "wt",
        prompt_file,
        tmp_path / "result.json",
        tmp_path / "last.md",
    )

    assert command == [
        "fake-agy",
        "--dangerously-skip-permissions",
        "--sandbox",
        "--add-dir",
        str(tmp_path / "wt"),
        "--print",
        "Task: bd-1\nHello prompt content",
    ]


def test_agent_command_can_resume_session_antigravity(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Hello prompt content", encoding="utf-8")

    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "antigravity",
                "antigravity_command": "fake-agy",
                "antigravity_resume_args": [
                    "--conversation",
                    "{session_id}",
                    "--print",
                    "{prompt_content}",
                ],
            }
        }
    )

    command = _agent_command(
        config,
        tmp_path / "wt",
        prompt_file,
        tmp_path / "result.json",
        tmp_path / "last.md",
        resume_session_id="session-123",
    )

    assert command == [
        "fake-agy",
        "--conversation",
        "session-123",
        "--print",
        "Hello prompt content",
    ]


def test_agent_command_expands_user_path_antigravity(tmp_path: Path, monkeypatch) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Hello", encoding="utf-8")
    
    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "antigravity",
                "antigravity_command": "~/bin/fake-agy",
                "antigravity_args": ["{prompt}"],
            }
        }
    )

    monkeypatch.setenv("HOME", "/home/testuser")

    command = _agent_command(
        config,
        tmp_path / "wt",
        prompt_file,
        tmp_path / "result.json",
        tmp_path / "last.md",
    )

    assert command[0] == "/home/testuser/bin/fake-agy"
