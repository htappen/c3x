from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from c3x import cli
from c3x.beads import BeadSummary
from c3x.schema import RunRecord, WorkerResult


class _FakeBeads:
    def show(self, task_id: str) -> object:
        return object()

    def set_status(self, task_id: str, status: str) -> None:
        return None

    def add_labels(self, task_id: str, labels: list[str]) -> None:
        return None

    def remove_labels(self, task_id: str, labels: list[str]) -> None:
        return None

    def add_note(self, task_id: str, note: str) -> None:
        return None

    def close(self, task_id: str, note: str) -> None:
        return None


class _StatusBeads:
    def __init__(self) -> None:
        self._active = [
            BeadSummary(id="bd-1", title="inbox", labels=("flow", "inbox", "idea")),
            BeadSummary(id="bd-2", title="question", labels=("flow", "question")),
            BeadSummary(id="bd-3", title="running", labels=("flow", "running")),
            BeadSummary(id="bd-4", title="reviewing", labels=("flow", "reviewing")),
            BeadSummary(id="bd-5", title="blocked", labels=("flow", "blocked")),
        ]

    def list_active(self) -> list[BeadSummary]:
        return list(self._active)

    def ready(self) -> list[BeadSummary]:
        return [BeadSummary(id="bd-6", title="ready", labels=("flow", "ready"))]


class _RecordingBeads:
    def __init__(self) -> None:
        self.items: dict[str, BeadSummary] = {}
        self.notes: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, list[str]]] = []
        self.removed_labels: list[tuple[str, list[str]]] = []
        self.statuses: list[tuple[str, str]] = []
        self.closed: list[tuple[str, str]] = []
        self.next_id = 1

    def create_inbox_item(
        self,
        title: str,
        *,
        description: str | None = None,
        priority: int = 2,
    ) -> dict[str, str]:
        item_id = f"bd-{self.next_id}"
        self.next_id += 1
        self.items[item_id] = BeadSummary(
            id=item_id,
            title=title,
            description=description,
            priority=priority,
            labels=("flow", "inbox", "idea", "unreviewed", "human-feedback"),
        )
        return {"id": item_id}

    def create_task(
        self,
        title: str,
        *,
        description: str,
        labels: list[str],
        issue_type: str = "task",
        priority: int = 2,
    ) -> dict[str, str]:
        item_id = f"bd-{self.next_id}"
        self.next_id += 1
        self.items[item_id] = BeadSummary(
            id=item_id,
            title=title,
            description=description,
            priority=priority,
            labels=tuple(labels),
        )
        return {"id": item_id}

    def list_open(self) -> list[BeadSummary]:
        return list(self.items.values())

    def list_active(self) -> list[BeadSummary]:
        return list(self.items.values())

    def show(self, task_id: str) -> BeadSummary:
        return self.items[task_id]

    def add_note(self, task_id: str, note: str) -> None:
        self.notes.append((task_id, note))

    def add_labels(self, task_id: str, labels: list[str]) -> None:
        self.added_labels.append((task_id, labels))
        item = self.items[task_id]
        self.items[task_id] = replace(item, labels=tuple({*item.labels, *labels}))

    def remove_labels(self, task_id: str, labels: list[str]) -> None:
        self.removed_labels.append((task_id, labels))
        item = self.items[task_id]
        self.items[task_id] = replace(
            item,
            labels=tuple(label for label in item.labels if label not in labels),
        )

    def set_status(self, task_id: str, status: str) -> None:
        self.statuses.append((task_id, status))
        item = self.items[task_id]
        self.items[task_id] = replace(item, status=status)

    def close(self, task_id: str, note: str) -> None:
        self.closed.append((task_id, note))
        self.items.pop(task_id, None)


def test_start_warns_when_root_branch_is_main(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "current_branch", lambda root: "main")
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "_beads", lambda root: _FakeBeads())
    monkeypatch.setattr(
        cli,
        "start_worker",
        lambda root, config, task: RunRecord(
            task_id="bd-1",
            branch="c3x/bd-1-fix-auth",
            worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-auth"),
            prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
            result=str(tmp_path / ".flow" / "runs" / "bd-1" / "result.json"),
            last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last.md"),
        ),
    )

    result = runner.invoke(cli.app, ["start", "bd-1"])

    assert result.exit_code == 0
    assert "root worktree is on `main`" in result.stdout


def test_land_warns_when_root_branch_is_head(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    committed_worktrees: list[Path] = []
    removed_worktrees: list[tuple[Path, bool]] = []
    deleted_branches: list[str] = []
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "current_branch", lambda root: "HEAD")
    monkeypatch.setattr(cli, "_beads", lambda root: _FakeBeads())
    monkeypatch.setattr(
        cli,
        "commit_worktree_changes",
        lambda worktree, message: committed_worktrees.append(worktree),
    )
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: None)
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda root, message: None)
    monkeypatch.setattr(
        cli,
        "remove_worktree",
        lambda root, path, force=False: removed_worktrees.append((path, force)),
    )
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: deleted_branches.append(branch))
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-auth"
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-auth",
        worktree=str(worktree),
        prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
        result=str(tmp_path / ".flow" / "runs" / "bd-1" / "result.json"),
        last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last.md"),
        status="reviewed",
    )
    record.save(tmp_path / ".flow" / "runs" / "bd-1" / "run.json")

    result = runner.invoke(cli.app, ["land", "bd-1"])

    assert result.exit_code == 0
    assert "root worktree is on `HEAD`" in result.stdout
    assert committed_worktrees == [worktree]
    assert removed_worktrees == [(worktree, True)]
    assert deleted_branches == ["c3x/bd-1-fix-auth"]


def test_add_no_validate_leaves_feedback_unplanned(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["add", "--no-validate", "fix"])

    assert result.exit_code == 0
    assert "Added bd-1" in result.stdout
    assert len(beads.items) == 1
    assert not beads.closed


def test_add_validate_asks_clarification_then_plans(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["add", "fix"], input="fix the checkout refresh bug\n")

    assert result.exit_code == 0
    assert any("Clarification from bd-2" in note for _, note in beads.notes)
    assert any(labels == ["planned"] for _, labels in beads.added_labels)
    assert ("bd-1", "Planned as bd-3") in beads.closed


def test_status_renders_bucket_counts(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _StatusBeads()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "c3x activity" in result.stdout
    assert "Supervisor" in result.stdout
    assert "Inbox" in result.stdout
    assert "Questions" in result.stdout
    assert "Max parallel workers" in result.stdout
    assert "3" in result.stdout


def test_status_renders_supervisor_activity_and_worker_latest_message(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _StatusBeads()
    run_dir = tmp_path / ".flow" / "runs" / "bd-3"
    last_message = run_dir / "last-message.md"
    last_message.parent.mkdir(parents=True)
    last_message.write_text("Editing src/c3x/cli.py\nRunning pytest next", encoding="utf-8")
    RunRecord(
        task_id="bd-3",
        branch="c3x/bd-3-running",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-3-running"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(last_message),
        pid=1234,
    ).save(run_dir / "run.json")
    cli._write_activity(tmp_path, "dispatching worker bd-3")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "dispatching worker bd-3" in result.stdout
    assert "bd-3" in result.stdout
    assert "1234" in result.stdout
    assert "Editing src/c3x/cli.py Running pytest next" in result.stdout


def test_blocked_lists_flow_blocked_items_with_reason(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix worker",
        status="in_progress",
        priority=1,
        labels=("flow", "blocked", "blocker-result-missing"),
        notes=(
            "Worker exited without writing result.json.\n"
            "ERROR: You've hit your usage limit. Try again later."
        ),
    )
    beads.items["bd-2"] = BeadSummary(id="bd-2", title="ready", labels=("flow", "ready"))
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["blocked"])

    assert result.exit_code == 0
    assert "bd-1" in result.stdout
    assert "fix worker" in result.stdout
    assert "result missing" in result.stdout
    assert "Codex usage limit" in result.stdout
    assert "bd-2" not in result.stdout


def test_blocked_reports_empty_state(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="ready", labels=("flow", "ready"))
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["blocked"])

    assert result.exit_code == 0
    assert "No blocked c3x flow tasks" in result.stdout


def test_run_once_does_not_overwrite_tick_activity_with_waiting(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_build_status_view", lambda root: "")
    monkeypatch.setattr(
        cli,
        "_supervisor_tick",
        lambda root, *, dispatch: cli._write_activity(root, "tick complete; critic tasks OK"),
    )

    result = runner.invoke(cli.app, ["run", "--once"])

    assert result.exit_code == 0
    activity = cli._read_activity(tmp_path)
    assert activity["supervisor"] == "tick complete; critic tasks OK"
    assert "waiting" not in activity["supervisor"]


def test_supervisor_tick_records_critic_outcome(monkeypatch, tmp_path: Path) -> None:
    beads = _StatusBeads()
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "_import_finished_results", lambda root, beads: None)
    monkeypatch.setattr(cli, "_plan_inbox", lambda root, beads: None)
    monkeypatch.setattr(cli, "_maybe_warn_stuck", lambda root, beads: None)

    cli._supervisor_tick(tmp_path, dispatch=False)

    activity = cli._read_activity(tmp_path)
    assert activity["supervisor"] == "tick complete; critic tasks OK"


def test_answer_marks_blocking_item_clarified(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "inbox", "idea"))
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Clarify: fix",
        description="Blocks: bd-1\n\nNeed details.",
        labels=("flow", "question", "needs-human-clarification"),
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["answer", "bd-2", "fix the checkout refresh bug"])

    assert result.exit_code == 0
    assert ("bd-1", ["clarified"]) in beads.added_labels
    assert ("bd-2", ["question", "needs-human-clarification"]) in beads.removed_labels
    assert ("bd-2", "Answered human clarification") in beads.closed


def test_import_blocks_exited_worker_missing_result(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    run_dir.mkdir(parents=True)
    last_message = run_dir / "last-message.md"
    last_message.write_text("Could not write result.json; path is read-only.", encoding="utf-8")
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(last_message),
        pid=12345,
    )
    record.save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)

    cli._import_finished_results(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "blocked"
    assert saved.outcome == "missing-result"
    assert ("bd-1", ["flow", "blocked", "blocker-result-missing"]) in beads.added_labels
    assert ("bd-1", ["running", "reviewing"]) in beads.removed_labels
    assert any("Could not write result.json" in note for _, note in beads.notes)


def test_import_copies_worktree_result_to_run_directory(tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worker_result = worktree / ".c3x" / "result.json"
    worker_result.parent.mkdir(parents=True)
    worker_result.write_text(
        WorkerResult(
            task_id="bd-1",
            status="completed",
            summary="Fixed it",
            task_kind="bug",
            confidence="high",
        ).model_dump_json(),
        encoding="utf-8",
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worker_result),
        last_message=str(run_dir / "last-message.md"),
        pid=12345,
    ).save(run_dir / "run.json")

    cli._import_finished_results(tmp_path, beads)

    canonical_result = tmp_path / ".flow" / "runs" / "bd-1" / "result.json"
    saved = RunRecord.load(run_dir / "run.json")
    assert canonical_result.exists()
    assert WorkerResult.model_validate_json(canonical_result.read_text(encoding="utf-8")).summary == "Fixed it"
    assert saved.status == "completed"
    assert ("bd-1", ["flow", "reviewing", "completed-by-agent"]) in beads.added_labels


def test_recover_interrupted_worker_restarts_dead_running_attempt(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix" / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        pid=12345,
        status="running",
        attempt=1,
    ).save(run_dir / "run.json")

    def fake_start_worker(root: Path, config: object, task: BeadSummary) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-attempt-2",
            worktree=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            pid=67890,
            attempt=2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    cli._recover_interrupted_workers(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.attempt == 2
    assert saved.pid == 67890
    assert (tmp_path / ".flow" / "runs" / "bd-1-attempt-1" / "run.json").exists()
    assert ("bd-1", "in_progress") in beads.statuses
    assert ("bd-1", ["flow", "running", "attempt-2"]) in beads.added_labels


def test_kill_workers_dry_run_lists_live_recorded_workers(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    records = [
        RunRecord(
            task_id="bd-1",
            branch="c3x/bd-1-fix",
            worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
            prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
            result=str(tmp_path / ".flow" / "runs" / "bd-1" / "result.json"),
            last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last-message.md"),
            pid=12345,
            status="running",
        )
    ]
    killed: list[int] = []
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_run_records", lambda root: records)
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: True)
    monkeypatch.setattr(cli, "_worker_process_targets", lambda pid: [pid, 12346])
    monkeypatch.setattr(cli, "_kill_worker_process_tree", lambda pid, force=False: killed.append(pid) or [pid])

    result = runner.invoke(cli.app, ["kill", "--dry-run"])

    assert result.exit_code == 0
    assert "Would send SIGTERM" in result.stdout
    assert "12345, 12346" in result.stdout
    assert killed == []


def test_kill_workers_sends_signal_to_live_recorded_workers(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    records = [
        RunRecord(
            task_id="bd-1",
            branch="c3x/bd-1-fix",
            worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
            prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
            result=str(tmp_path / ".flow" / "runs" / "bd-1" / "result.json"),
            last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last-message.md"),
            pid=12345,
            status="running",
        )
    ]
    killed: list[tuple[int, bool]] = []
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_run_records", lambda root: records)
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: True)
    monkeypatch.setattr(cli, "_kill_worker_process_tree", lambda pid, force=False: killed.append((pid, force)) or [pid])

    result = runner.invoke(cli.app, ["kill", "--force"])

    assert result.exit_code == 0
    assert "Sent SIGKILL" in result.stdout
    assert killed == [(12345, True)]


def test_kill_worker_process_tree_avoids_shared_process_group(monkeypatch) -> None:
    killed_groups: list[tuple[int, int]] = []
    killed_pids: list[tuple[int, int]] = []
    monkeypatch.setattr(cli, "_worker_process_targets", lambda pid: [pid, 12346])
    monkeypatch.setattr(cli.os, "getpgid", lambda pid: 99999)
    monkeypatch.setattr(cli.os, "killpg", lambda pgid, sig: killed_groups.append((pgid, sig)))
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: killed_pids.append((pid, sig)))

    killed = cli._kill_worker_process_tree(12345, force=True)

    assert killed == [12345, 12346]
    assert killed_groups == []
    assert killed_pids == [(12346, cli.signal.SIGKILL), (12345, cli.signal.SIGKILL)]


def test_retry_archives_current_run_and_starts_fresh_attempt(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="blocked",
        labels=("flow", "blocked", "blocker-result-missing"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(run_dir / "run.json")

    def fake_start_worker(root: Path, config: object, task: BeadSummary) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-attempt-2",
            worktree=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1"])

    assert result.exit_code == 0
    assert (tmp_path / ".flow" / "runs" / "bd-1-attempt-1" / "run.json").exists()
    assert RunRecord.load(run_dir / "run.json").attempt == 2
    assert ("bd-1", "open") in beads.statuses
    assert ("bd-1", "in_progress") in beads.statuses
    assert ("bd-1", ["flow", "running", "attempt-2"]) in beads.added_labels
    assert any("blocker-result-missing" in labels for item_id, labels in beads.removed_labels if item_id == "bd-1")


def test_retry_all_retries_blocked_flow_tasks(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="one", labels=("flow", "blocked"))
    beads.items["bd-2"] = BeadSummary(id="bd-2", title="two", labels=("flow", "blocked"))
    started: list[str] = []

    def fake_start_worker(root: Path, config: object, task: BeadSummary) -> RunRecord:
        started.append(task.id)
        return RunRecord(
            task_id=task.id,
            branch=f"c3x/{task.id}",
            worktree=str(root / ".flow" / "worktrees" / task.id),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / task.id / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
        )

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "--all"])

    assert result.exit_code == 0
    assert started == ["bd-1", "bd-2"]
    assert ("bd-1", "in_progress") in beads.statuses
    assert ("bd-2", "in_progress") in beads.statuses


def test_squash_task_squashes_landed_tip_segment(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    calls: list[tuple[str, str]] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
    ).save(run_dir / "run.json")
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="Fixed it").model_dump_json(),
        encoding="utf-8",
    )
    subjects = {
        "head": "Merge c3x/bd-1-fix",
        "checkpoint": "Checkpoint c3x ledger before merge",
        "base": "Previous work",
    }
    parents = {
        "head": ["checkpoint", "worker"],
        "checkpoint": ["base"],
        "base": [],
    }
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "ensure_rewrite_safe", lambda root: None)
    monkeypatch.setattr(cli, "rev_parse", lambda root, rev: "head")
    monkeypatch.setattr(cli, "commit_subject", lambda root, rev: subjects[rev])
    monkeypatch.setattr(cli, "commit_parents", lambda root, rev: parents[rev])
    monkeypatch.setattr(cli, "squash_head_to", lambda root, base, message: calls.append((base, message)))

    result = runner.invoke(cli.app, ["squash", "bd-1"])

    assert result.exit_code == 0
    assert calls == [("base", "Complete c3x task bd-1\n\nFixed it")]
    assert "Squashed bd-1: 2 commits" in result.stdout


def test_squash_task_does_not_require_task_branch_ref(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    calls: list[tuple[str, str]] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
    ).save(run_dir / "run.json")
    subjects = {
        "head": "Merge c3x/bd-1-fix",
        "close": "Close c3x task bd-1",
        "base": "Previous work",
    }
    parents = {
        "head": ["close", "worker"],
        "close": ["base"],
        "base": [],
    }
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "ensure_rewrite_safe", lambda root: None)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: (_ for _ in ()).throw(Exception("missing ref")))
    monkeypatch.setattr(cli, "rev_parse", lambda root, rev: "head")
    monkeypatch.setattr(cli, "commit_subject", lambda root, rev: subjects[rev])
    monkeypatch.setattr(cli, "commit_parents", lambda root, rev: parents[rev])
    monkeypatch.setattr(cli, "squash_head_to", lambda root, base, message: calls.append((base, message)))

    result = runner.invoke(cli.app, ["squash", "bd-1"])

    assert result.exit_code == 0
    assert calls == [("base", "Complete c3x task bd-1")]


def test_squash_all_squashes_eligible_landed_tip_segment(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    calls: list[tuple[str, str]] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
    ).save(run_dir / "run.json")
    subjects = {
        "head": "Close c3x task bd-1",
        "merge": "Merge c3x/bd-1-fix",
        "base": "Previous work",
    }
    parents = {
        "head": ["merge"],
        "merge": ["base", "worker"],
        "base": [],
    }
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "ensure_rewrite_safe", lambda root: None)
    monkeypatch.setattr(cli, "rev_parse", lambda root, rev: "head")
    monkeypatch.setattr(cli, "commit_subject", lambda root, rev: subjects[rev])
    monkeypatch.setattr(cli, "commit_parents", lambda root, rev: parents[rev])
    monkeypatch.setattr(cli, "squash_head_to", lambda root, base, message: calls.append((base, message)))

    result = runner.invoke(cli.app, ["squash", "--all"])

    assert result.exit_code == 0
    assert calls == [("base", "Complete c3x task bd-1")]


def test_cleanup_removes_superseded_attempt_run_directory(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    removed_worktrees: list[Path] = []
    deleted_branches: list[str] = []
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
    archived_dir.mkdir(parents=True)
    archived_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(archived_worktree),
        prompt=str(archived_dir / "prompt.md"),
        result=str(archived_dir / "result.json"),
        last_message=str(archived_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(archived_dir / "run.json")
    current_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
        prompt=str(current_dir / "prompt.md"),
        result=str(current_dir / "result.json"),
        last_message=str(current_dir / "last-message.md"),
        status="completed",
        attempt=2,
    ).save(current_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: True)
    monkeypatch.setattr(cli, "remove_worktree", lambda root, worktree, force=False: removed_worktrees.append(worktree))
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: deleted_branches.append(branch))

    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    assert not archived_dir.exists()
    assert removed_worktrees == [archived_worktree]
    assert deleted_branches == ["c3x/bd-1-fix"]


def test_cleanup_dry_run_leaves_superseded_attempt(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(archived_dir / "prompt.md"),
        result=str(archived_dir / "result.json"),
        last_message=str(archived_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(archived_dir / "run.json")
    current_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
        prompt=str(current_dir / "prompt.md"),
        result=str(current_dir / "result.json"),
        last_message=str(current_dir / "last-message.md"),
        status="reviewed",
        attempt=2,
    ).save(current_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: True)

    result = runner.invoke(cli.app, ["cleanup", "--dry-run"])

    assert result.exit_code == 0
    assert archived_dir.exists()
    assert "Would clean" in result.stdout


def test_cleanup_removes_landed_worktree_without_deleting_current_run(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    removed_worktrees: list[Path] = []
    deleted_branches: list[str] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
        attempt=1,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: True)
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: removed_worktrees.append(path))
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: deleted_branches.append(branch))

    result = runner.invoke(cli.app, ["cleanup", "bd-1"])

    assert result.exit_code == 0
    assert (run_dir / "run.json").exists()
    assert removed_worktrees == [worktree]
    assert deleted_branches == ["c3x/bd-1-fix"]


def test_cleanup_removes_landed_worktree_when_branch_is_already_missing(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    removed_worktrees: list[Path] = []
    deleted_branches: list[str] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
        attempt=1,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli,
        "is_ancestor",
        lambda root, ancestor, descendant: (_ for _ in ()).throw(
            cli.GitError("fatal: Not a valid object name c3x/bd-1-fix")
        ),
    )
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: removed_worktrees.append(path))
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: deleted_branches.append(branch))

    result = runner.invoke(cli.app, ["cleanup", "bd-1"])

    assert result.exit_code == 0
    assert "landed worktree with missing branch" in result.stdout
    assert removed_worktrees == [worktree]
    assert deleted_branches == ["c3x/bd-1-fix"]


def test_unstick_defaults_to_dry_run_with_cheap_verification(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
        finished_at="2026-05-25T00:00:00+00:00",
    ).save(run_dir / "run.json")
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, branch, descendant: True)
    monkeypatch.setattr(cli, "run_verification", lambda root, commands: [])

    result = runner.invoke(cli.app, ["unstick"])

    assert result.exit_code == 0
    assert "bd-1" in result.stdout
    assert "Dry run only" in result.stdout
    assert "bd-1" in beads.items


def test_unstick_fix_skips_recorded_verification_gap_by_default(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
        finished_at="2026-05-25T00:00:00+00:00",
    ).save(run_dir / "run.json")
    (run_dir / "result.json").write_text(
        (
            WorkerResult(
                task_id="bd-1",
                status="completed",
                summary="done",
                verification=["npm test (failed with ERR_MODULE_NOT_FOUND)"],
            ).model_dump_json()
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, branch, descendant: True)

    result = runner.invoke(cli.app, ["unstick", "--fix"])

    assert result.exit_code == 0
    assert "Skipped" in result.stdout
    assert "verification has gaps" in result.stdout
    assert "bd-1" in beads.items


def test_unstick_fix_can_accept_recorded_verification_gap(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
        finished_at="2026-05-25T00:00:00+00:00",
    ).save(run_dir / "run.json")
    (run_dir / "result.json").write_text(
        (
            WorkerResult(
                task_id="bd-1",
                status="completed",
                summary="done",
                verification=["npm test (failed with ERR_MODULE_NOT_FOUND)"],
            ).model_dump_json()
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, branch, descendant: True)

    result = runner.invoke(cli.app, ["unstick", "--fix", "--accept-verification-gaps"])

    assert result.exit_code == 0
    assert "Repaired" in result.stdout
    assert "bd-1" not in beads.items
    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "landed"


def test_stuck_detector_uses_notice_cooldown(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
        finished_at="2026-05-25T00:00:00+00:00",
    ).save(run_dir / "run.json")
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "is_ancestor", lambda root, branch, descendant: True)

    cli._maybe_warn_stuck(tmp_path, beads)
    first_notice = (tmp_path / ".flow" / "stuck-notice.json").read_text(encoding="utf-8")
    cli._maybe_warn_stuck(tmp_path, beads)

    assert (tmp_path / ".flow" / "stuck-notice.json").read_text(encoding="utf-8") == first_notice


def test_cheap_verification_treats_conflict_marker_rg_no_matches_as_pass(tmp_path: Path) -> None:
    target = tmp_path / "file.js"
    target.write_text("const ok = true;\n", encoding="utf-8")

    issues = cli._cheap_verification_issues(tmp_path, [r"rg -n '<<<<<<<|=======$|>>>>>>>' file.js"])

    assert issues == []


def test_cleanup_repairs_landed_unmerged_branch_after_confirmation(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    calls: list[tuple[str, object]] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
        attempt=1,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)
    monkeypatch.setattr(cli, "branch_diff_summary", lambda root, branch: "Diff stat:\n file.ts | 2 +")
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: calls.append(("commit_worktree", path)))
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: calls.append(("merge", branch)))
    monkeypatch.setattr(
        cli,
        "remove_worktree",
        lambda root, path, force=False: calls.append(("remove_worktree", (path, force))),
    )
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: calls.append(("delete_branch", branch)))

    result = runner.invoke(cli.app, ["cleanup", "bd-1"], input="y\n")

    assert result.exit_code == 0
    assert "Diff stat" in result.stdout
    assert ("commit_worktree", worktree) in calls
    assert ("merge", "c3x/bd-1-fix") in calls
    assert ("remove_worktree", (worktree, True)) in calls
    assert ("delete_branch", "c3x/bd-1-fix") in calls


def test_cleanup_skips_landed_unmerged_branch_when_declined(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    calls: list[tuple[str, object]] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)
    monkeypatch.setattr(cli, "branch_diff_summary", lambda root, branch: "Diff stat:\n file.ts | 2 +")
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: calls.append(("merge", branch)))

    result = runner.invoke(cli.app, ["cleanup", "bd-1"], input="n\n")

    assert result.exit_code == 0
    assert "Skipped" in result.stdout
    assert calls == []


def test_auto_land_commits_merges_and_force_cleans_worker_worktree(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        labels=("flow", "reviewing", "reviewed"),
    )
    calls: list[tuple[str, object]] = []
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: calls.append(("commit_worktree", path)))
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: calls.append(("merge", branch)))
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda root, message: calls.append(("commit_ledger", message)))
    monkeypatch.setattr(beads, "close", lambda task_id, note: calls.append(("close", task_id)))
    monkeypatch.setattr(
        cli,
        "remove_worktree",
        lambda root, path, force=False: calls.append(("remove_worktree", (path, force))),
    )
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: calls.append(("delete_branch", branch)))

    cli._auto_land(tmp_path, beads, cleanup_done=True)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "landed"
    assert ("commit_worktree", worktree) in calls
    assert ("merge", "c3x/bd-1-fix") in calls
    assert ("remove_worktree", (worktree, True)) in calls
    assert ("delete_branch", "c3x/bd-1-fix") in calls


def test_auto_land_marks_merge_conflict_blocker(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        labels=("flow", "reviewing", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: None)

    def fail_merge(root: Path, branch: str) -> None:
        raise cli.GitMergeConflict(branch, ["app.py"], "CONFLICT (content): app.py")

    monkeypatch.setattr(cli, "merge_branch", fail_merge)

    cli._auto_land(tmp_path, beads, cleanup_done=True)

    assert ("bd-1", ["flow", "blocked", "land-blocked", "blocker-merge-conflict"]) in beads.added_labels
    assert any("app.py" in note for task_id, note in beads.notes if task_id == "bd-1")


def test_resolve_conflict_starts_resolver_attempt(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        labels=("flow", "blocked", "land-blocked", "blocker-merge-conflict"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
        attempt=1,
    ).save(run_dir / "run.json")
    (run_dir / "result.json").write_text('{"task_id": "bd-1", "status": "completed"}\n', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_start_conflict_resolver(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        source_branch: str,
        target_branch: str,
        target_revision: str,
        original_result: str,
    ) -> RunRecord:
        captured.update(
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "target_revision": target_revision,
                "original_result": original_result,
            }
        )
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-conflict-attempt-2",
            worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-conflict-attempt-2"),
            prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
            result=str(tmp_path / ".flow" / "runs" / "bd-1" / "result.json"),
            last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last-message.md"),
            attempt=2,
        )
        record.save(tmp_path / ".flow" / "runs" / "bd-1" / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_warn_if_risky_flow_branch", lambda root: None)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "current_branch", lambda root: "main")
    monkeypatch.setattr(cli, "rev_parse", lambda root, rev: "abc123")
    monkeypatch.setattr(cli, "start_conflict_resolver", fake_start_conflict_resolver)

    result = runner.invoke(cli.app, ["resolve-conflict", "bd-1"])

    assert result.exit_code == 0
    assert captured["source_branch"] == "c3x/bd-1-fix"
    assert captured["target_branch"] == "main"
    assert captured["target_revision"] == "abc123"
    assert "completed" in str(captured["original_result"])
    assert (tmp_path / ".flow" / "runs" / "bd-1-attempt-1" / "run.json").exists()
    assert ("bd-1", "in_progress") in beads.statuses
    assert any("conflict-resolver" in labels for task_id, labels in beads.added_labels if task_id == "bd-1")
