from pathlib import Path

from c3x import agent
from c3x.agent import _agent_command
from c3x.agent import _next_attempt
from c3x.agent import _reviewer_prompt
from c3x.agent import run_reviewer
from c3x.agent import start_conflict_resolver
from c3x.agent import start_worker
from c3x.agent import _worker_prompt
from c3x.schema import RunRecord, WorkerResult
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


def test_agent_command_uses_reviewer_model_for_reviewer_tasks(tmp_path: Path) -> None:
    config = C3xConfig.model_validate(
        {
            "models": {"codex": {"worker": "worker-model", "reviewer": "reviewer-model"}},
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["--model", "{model}", "{prompt}"],
            },
        }
    )

    command = _agent_command(
        config,
        tmp_path / "wt",
        tmp_path / "prompt.md",
        tmp_path / "result.json",
        tmp_path / "last.md",
        task_type="reviewer",
    )

    assert command == ["fake-codex", "--model", "reviewer-model", str(tmp_path / "prompt.md")]


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


def test_reviewer_prompt_uses_review_skill_and_embeds_requirements(tmp_path: Path) -> None:
    prompt = _reviewer_prompt(
        BeadSummary(
            id="bd-1",
            title="Fix auth",
            description="Redirects preserve query params.",
            acceptance="Regression test passes.",
        ),
        WorkerResult(task_id="bd-1", status="completed", summary="Changed redirects"),
        tmp_path / "review.json",
        diff_summary="Diff stat",
    )

    assert prompt.startswith("/review\n\n")
    assert "Use the `flow-reviewer` skill." in prompt
    assert "Redirects preserve query params." in prompt
    assert "Regression test passes." in prompt
    assert "Check each requirement explicitly" in prompt


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

    assert "Do not run other Beads commands" in prompt
    assert "bd show bd-1" in prompt
    assert "Do not run `git commit`, `git push`, `git pull`, `git merge`" in prompt
    assert "The supervisor will commit and merge" in prompt


def test_worker_prompt_requires_agents_instructions(tmp_path: Path) -> None:
    prompt = _worker_prompt(
        BeadSummary(id="bd-1", title="Fix auth"),
        tmp_path / "result.json",
    )

    assert "read and follow the root `AGENTS.md`" in prompt
    assert "nested\n`AGENTS.md` files" in prompt


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
    assert record.provider == "codex"
    assert record.task_type == "worker"
    assert Path(record.prompt) == tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "prompt.md"
    assert Path(record.last_message) == tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "last-message.md"
    assert Path(record.result).name == "bd-1-result.json"
    assert popen_kwargs["start_new_session"] is True


def test_start_worker_writes_process_logs_under_attempt_folder(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        launched["command"] = command
        launched["stdout"] = kwargs["stdout"]
        launched["stderr"] = kwargs["stderr"]
        return FakeProcess()

    monkeypatch.setattr(agent, "create_worktree", lambda root, branch, worktree: worktree.mkdir(parents=True))
    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}", "{last_message}"],
            }
        }
    )

    record = start_worker(tmp_path, config, BeadSummary(id="bd-1", title="Fix auth"))
    log_dir = tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1"

    assert launched["command"] == ["fake-codex", str(log_dir / "prompt.md"), str(log_dir / "last-message.md")]
    assert Path(launched["stdout"].name) == log_dir / "stdout.log"
    assert Path(launched["stderr"].name) == log_dir / "stderr.log"
    assert Path(record.prompt).parent == log_dir
    launched["stdout"].close()
    launched["stderr"].close()


def test_start_conflict_resolver_uses_separate_log_folder(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        launched["command"] = command
        launched["stdout"] = kwargs["stdout"]
        launched["stderr"] = kwargs["stderr"]
        return FakeProcess()

    monkeypatch.setattr(
        agent,
        "create_conflict_resolution_worktree",
        lambda root, branch, source_branch, worktree: worktree.mkdir(parents=True) or ["app.py"],
    )
    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}", "{last_message}"],
            }
        }
    )

    record = start_conflict_resolver(
        tmp_path,
        config,
        BeadSummary(id="bd-1", title="Fix auth"),
        source_branch="c3x/bd-1-fix-auth",
        target_branch="main",
        target_revision="abc123",
        original_result="{}",
        attempt=2,
    )
    log_dir = tmp_path / ".flow" / "runs" / "bd-1" / "conflict-resolver-attempt-2"

    assert launched["command"] == ["fake-codex", str(log_dir / "prompt.md"), str(log_dir / "last-message.md")]
    assert Path(launched["stdout"].name) == log_dir / "stdout.log"
    assert Path(launched["stderr"].name) == log_dir / "stderr.log"
    assert Path(record.prompt).parent == log_dir
    assert record.task_type == "conflict_resolver"
    launched["stdout"].close()
    launched["stderr"].close()


def test_start_worker_uses_worker_provider_override(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(agent, "create_worktree", lambda root, branch, worktree: worktree.mkdir(parents=True))
    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "codex",
                "provider_overrides": {"worker": "antigravity"},
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}"],
                "antigravity_command": "fake-agy",
                "antigravity_args": ["--print", "{prompt_content}"],
            }
        }
    )

    record = start_worker(tmp_path, config, BeadSummary(id="bd-1", title="Fix auth"))

    assert launched["command"][0] == "fake-agy"
    assert record.provider == "antigravity"
    assert record.task_type == "worker"


def test_run_reviewer_writes_result_inside_worktree_c3x(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "last-message.md"),
    )
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}", "{result}"],
            }
        }
    )

    def fake_run(command: list[str], **kwargs: object) -> object:
        result = Path(command[-1])
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        result.write_text(
            '{"task_id":"bd-1","status":"approved","summary":"ok","requirements":[],"issues":[]}',
            encoding="utf-8",
        )

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr(agent.subprocess, "run", fake_run)

    review = run_reviewer(
        tmp_path,
        config,
        BeadSummary(id="bd-1", title="Fix auth"),
        WorkerResult(task_id="bd-1", status="completed", summary="done"),
        record=record,
        diff_summary="diff",
    )

    result = worktree / ".c3x" / "bd-1-reviewer-result.json"
    assert review.status == "approved"
    assert result.exists()
    assert captured["cwd"] == worktree
    assert captured["command"][-1] == str(result)
    assert not (tmp_path / ".flow" / "runs" / "bd-1" / "reviewer-attempt-1" / "result.json").exists()


def test_run_reviewer_recovers_result_from_last_message_when_file_missing(monkeypatch, tmp_path: Path) -> None:
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "last-message.md"),
    )
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}", "{result}", "{last_message}"],
            }
        }
    )

    def fake_run(command: list[str], **kwargs: object) -> object:
        last_message = Path(command[-1])
        last_message.write_text(
            "Could not write result.json.\n\n"
            "```json\n"
            '{"task_id":"bd-1","status":"blocked","summary":"needs fix","requirements":[],"issues":[{"title":"fix","description":"bug","severity":"high"}]}\n'
            "```\n",
            encoding="utf-8",
        )

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr(agent.subprocess, "run", fake_run)

    review = run_reviewer(
        tmp_path,
        config,
        BeadSummary(id="bd-1", title="Fix auth"),
        WorkerResult(task_id="bd-1", status="completed", summary="done"),
        record=record,
        diff_summary="diff",
    )

    result = worktree / ".c3x" / "bd-1-reviewer-result.json"
    assert review.status == "blocked"
    assert result.exists()


def test_run_reviewer_recovers_result_from_review_result_link(monkeypatch, tmp_path: Path) -> None:
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    linked_result = worktree / "review-result.json"
    linked_result.write_text(
        '{"task_id":"bd-1","status":"blocked","summary":"needs fix","requirements":[],"issues":[{"title":"fix","description":"bug","severity":"high"}]}',
        encoding="utf-8",
    )
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "worker-attempt-1" / "last-message.md"),
    )
    config = C3xConfig.model_validate(
        {
            "agents": {
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}", "{result}", "{last_message}"],
            }
        }
    )

    def fake_run(command: list[str], **kwargs: object) -> object:
        Path(command[-1]).write_text(f"Review JSON prepared at [review-result.json]({linked_result}:1).\n", encoding="utf-8")

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr(agent.subprocess, "run", fake_run)

    review = run_reviewer(
        tmp_path,
        config,
        BeadSummary(id="bd-1", title="Fix auth"),
        WorkerResult(task_id="bd-1", status="completed", summary="done"),
        record=record,
        diff_summary="diff",
    )

    assert review.status == "blocked"


def test_next_attempt_uses_record_and_worktree_suffixes(tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-4"),
        prompt=str(run_dir / "prompt.md"),
        result=str(
            tmp_path
            / ".flow"
            / "worktrees"
            / "c3x-bd-1-fix-attempt-4"
            / ".c3x"
            / "result.json"
        ),
        last_message=str(run_dir / "last-message.md"),
        attempt=2,
    ).save(run_dir / "run.json")
    (tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-5").mkdir(parents=True)

    assert _next_attempt(tmp_path, "bd-1") == 6


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


def test_agent_command_uses_task_type_provider_override(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Task: bd-1\nHello prompt content", encoding="utf-8")

    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "codex",
                "provider_overrides": {"worker": "antigravity"},
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}"],
                "antigravity_command": "fake-agy",
                "antigravity_args": ["--print", "{prompt_content}"],
            }
        }
    )

    worker_command = _agent_command(
        config,
        tmp_path / "wt",
        prompt_file,
        tmp_path / "result.json",
        tmp_path / "last.md",
        task_type="worker",
    )
    resolver_command = _agent_command(
        config,
        tmp_path / "wt",
        prompt_file,
        tmp_path / "result.json",
        tmp_path / "last.md",
        task_type="conflict_resolver",
    )

    assert worker_command[0] == "fake-agy"
    assert resolver_command[0] == "fake-codex"


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


def test_agent_command_substitutes_runtime_paths_opencode(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Task: bd-1\nHello prompt content", encoding="utf-8")

    config = C3xConfig.model_validate(
        {
            "models": {"opencode": {"worker": "opencode/test-worker"}},
            "agents": {
                "provider": "opencode",
                "opencode_command": "fake-opencode",
                "opencode_args": [
                    "run",
                    "--model",
                    "{model}",
                    "--dir",
                    "{worktree}",
                    "--dangerously-skip-permissions",
                    "{prompt_content}",
                ],
            },
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
        "fake-opencode",
        "run",
        "--model",
        "opencode/test-worker",
        "--dir",
        str(tmp_path / "wt"),
        "--dangerously-skip-permissions",
        "Task: bd-1\nHello prompt content",
    ]


def test_agent_command_can_resume_session_opencode(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Hello prompt content", encoding="utf-8")

    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "opencode",
                "opencode_command": "fake-opencode",
                "opencode_resume_args": [
                    "run",
                    "--session",
                    "{session_id}",
                    "--model",
                    "{model}",
                    "--dir",
                    "{worktree}",
                    "{prompt_content}",
                ],
            },
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
        "fake-opencode",
        "run",
        "--session",
        "session-123",
        "--model",
        "opencode/gpt-5.1-codex",
        "--dir",
        str(tmp_path / "wt"),
        "Hello prompt content",
    ]


def test_start_worker_uses_opencode_provider_override_and_model(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(agent, "create_worktree", lambda root, branch, worktree: worktree.mkdir(parents=True))
    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)
    config = C3xConfig.model_validate(
        {
            "models": {"opencode": {"worker": "opencode/worker-model"}},
            "agents": {
                "provider": "codex",
                "provider_overrides": {"worker": "opencode"},
                "codex_command": "fake-codex",
                "codex_args": ["{prompt}"],
                "opencode_command": "fake-opencode",
                "opencode_args": ["run", "--model", "{model}", "{prompt_content}"],
            },
        }
    )

    record = start_worker(tmp_path, config, BeadSummary(id="bd-1", title="Fix auth"))

    assert launched["command"][:3] == ["fake-opencode", "run", "--model"]
    assert launched["command"][3] == "opencode/worker-model"
    assert record.provider == "opencode"
    assert record.task_type == "worker"


def test_agent_command_expands_user_path_opencode(tmp_path: Path, monkeypatch) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Hello", encoding="utf-8")

    config = C3xConfig.model_validate(
        {
            "agents": {
                "provider": "opencode",
                "opencode_command": "~/bin/fake-opencode",
                "opencode_args": ["{prompt}"],
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

    assert command[0] == "/home/testuser/bin/fake-opencode"
