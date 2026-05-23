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
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "current_branch", lambda root: "HEAD")
    monkeypatch.setattr(cli, "_beads", lambda root: _FakeBeads())
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: None)
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda root, message: None)
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-auth",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-auth"),
        prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
        result=str(tmp_path / ".flow" / "runs" / "bd-1" / "result.json"),
        last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last.md"),
        status="reviewed",
    )
    record.save(tmp_path / ".flow" / "runs" / "bd-1" / "run.json")

    result = runner.invoke(cli.app, ["land", "bd-1"])

    assert result.exit_code == 0
    assert "root worktree is on `HEAD`" in result.stdout


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
    assert "Inbox" in result.stdout
    assert "Questions" in result.stdout
    assert "Max parallel workers" in result.stdout
    assert "3" in result.stdout


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
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: removed_worktrees.append(path))
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: deleted_branches.append(branch))

    result = runner.invoke(cli.app, ["cleanup", "bd-1"])

    assert result.exit_code == 0
    assert (run_dir / "run.json").exists()
    assert removed_worktrees == [worktree]
    assert deleted_branches == ["c3x/bd-1-fix"]
