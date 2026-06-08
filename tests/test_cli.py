from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from c3x import cli
from c3x.beads import BeadSummary
from c3x.schema import ReviewResult, RunRecord, WorkerResult


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

    def dependencies(self, task_id: str, *, direction: str = "down", dep_type: str = "blocks") -> list[dict[str, str]]:
        return []


class _RecordingBeads:
    def __init__(self) -> None:
        self.items: dict[str, BeadSummary] = {}
        self.notes: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, list[str]]] = []
        self.removed_labels: list[tuple[str, list[str]]] = []
        self.statuses: list[tuple[str, str]] = []
        self.closed: list[tuple[str, str]] = []
        self.compacted: list[tuple[str, str]] = []
        self.blockers: list[tuple[str, str]] = []
        self.removed_blockers: list[tuple[str, str]] = []
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

    def ready(self) -> list[BeadSummary]:
        return [item for item in self.items.values() if "ready" in item.labels]

    def dependencies(self, task_id: str, *, direction: str = "down", dep_type: str = "blocks") -> list[dict[str, str]]:
        if direction != "down" or dep_type != "blocks":
            return []
        return [
            {"issue_id": task_id, "depends_on_id": blocker_id, "type": dep_type}
            for blocker_id, blocked_id in self.blockers
            if blocked_id == task_id
        ]

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

    def add_blocker(self, blocker_id: str, blocked_id: str) -> None:
        self.blockers.append((blocker_id, blocked_id))

    def remove_blocker(self, blocker_id: str, blocked_id: str) -> None:
        self.removed_blockers.append((blocker_id, blocked_id))
        if (blocker_id, blocked_id) in self.blockers:
            self.blockers.remove((blocker_id, blocked_id))

    def compact_issue(self, task_id: str, summary: str, *, issue: BeadSummary | None = None) -> None:
        self.compacted.append((task_id, summary))
        item = self.items[task_id]
        self.items[task_id] = replace(item, description=summary, notes="")


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
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: "landed123")
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


def test_land_requires_task_id_or_all(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)

    missing = runner.invoke(cli.app, ["land"])
    duplicate = runner.invoke(cli.app, ["land", "bd-1", "--all"])

    assert missing.exit_code == 1
    assert duplicate.exit_code == 1
    assert "pass a task id or --all" in missing.stdout
    assert "pass a task id or --all" in duplicate.stdout


def test_land_refuses_to_land_task_into_its_own_branch(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    branch = "c3x/bd-1-fix"
    RunRecord(
        task_id="bd-1",
        branch=branch,
        worktree=str(tmp_path),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_warn_if_risky_flow_branch", lambda root: None)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "current_branch", lambda root: branch)
    monkeypatch.setattr(cli, "worktree_has_changes", lambda root, ignored_prefixes=(): False)

    result = runner.invoke(cli.app, ["land", "bd-1", "--no-cleanup"])

    assert result.exit_code == 1
    assert "refusing to land bd-1 into task branch" in result.stdout
    assert not beads.closed


def test_land_records_landing_branch_and_revision(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix")
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "bd-1"
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
    )
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: None)
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: None)
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda root, message: None)
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: "landed123")
    monkeypatch.setattr(beads, "close", lambda task_id, note: beads.closed.append((task_id, note)))

    cli._land_record(tmp_path, beads, record, cleanup_done=False, close_note="landed")

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "landed"
    assert saved.landing_branch == "feature"
    assert saved.landed_revision == "landed123"


def test_land_nested_blocker_merges_into_original_ancestor_worktree(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="original", labels=("flow", "blocked"))
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="first blocker",
        description="Blocks: bd-1",
        labels=("flow", "blocked", "review-fix"),
    )
    beads.items["bd-3"] = BeadSummary(
        id="bd-3",
        title="nested blocker",
        description="Blocks: bd-2",
        labels=("flow", "reviewed", "review-fix"),
    )
    ancestor_worktree = tmp_path / ".flow" / "worktrees" / "bd-1"
    child_worktree = tmp_path / ".flow" / "worktrees" / "bd-3"
    ancestor_worktree.mkdir(parents=True)
    child_worktree.mkdir(parents=True)
    ancestor_run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-original",
        worktree=str(ancestor_worktree),
        prompt=str(ancestor_run_dir / "prompt.md"),
        result=str(ancestor_run_dir / "result.json"),
        last_message=str(ancestor_run_dir / "last-message.md"),
        status="blocked",
    ).save(ancestor_run_dir / "run.json")
    child_run_dir = tmp_path / ".flow" / "runs" / "bd-3"
    child = RunRecord(
        task_id="bd-3",
        branch="c3x/bd-3-nested",
        worktree=str(child_worktree),
        prompt=str(child_run_dir / "prompt.md"),
        result=str(child_run_dir / "result.json"),
        last_message=str(child_run_dir / "last-message.md"),
        status="reviewed",
    )
    merges: list[tuple[Path, str]] = []
    ledger_commits: list[Path] = []
    monkeypatch.setattr(cli, "current_branch", lambda path: "c3x/bd-1-original" if path == ancestor_worktree else "main")
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: None)
    monkeypatch.setattr(cli, "merge_branch", lambda path, branch: merges.append((path, branch)))
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda path, message: ledger_commits.append(path))
    monkeypatch.setattr(cli, "rev_parse", lambda path, revision: "landed123")
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: None)
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch: None)
    monkeypatch.setattr(beads, "close", lambda task_id, note: beads.closed.append((task_id, note)))

    cli._land_record(tmp_path, beads, child, cleanup_done=True, close_note="landed")

    saved = RunRecord.load(child_run_dir / "run.json")
    assert merges == [(ancestor_worktree, "c3x/bd-3-nested")]
    assert ledger_commits == [ancestor_worktree]
    assert saved.landing_branch == "c3x/bd-1-original"


def test_land_shared_ancestor_branch_does_not_remove_original_worktree(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="original", labels=("flow", "blocked"))
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="blocker",
        description="Blocks: bd-1",
        labels=("flow", "reviewed", "review-fix"),
    )
    worktree = tmp_path / ".flow" / "worktrees" / "bd-1"
    worktree.mkdir(parents=True)
    for task_id in ("bd-1", "bd-2"):
        run_dir = tmp_path / ".flow" / "runs" / task_id
        RunRecord(
            task_id=task_id,
            branch="c3x/bd-1-original",
            worktree=str(worktree),
            prompt=str(run_dir / "prompt.md"),
            result=str(run_dir / "result.json"),
            last_message=str(run_dir / "last-message.md"),
            status="blocked" if task_id == "bd-1" else "reviewed",
        ).save(run_dir / "run.json")
    child = RunRecord.load(tmp_path / ".flow" / "runs" / "bd-2" / "run.json")
    merges: list[tuple[Path, str]] = []
    removed: list[Path] = []
    monkeypatch.setattr(cli, "current_branch", lambda path: "c3x/bd-1-original")
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: None)
    monkeypatch.setattr(cli, "merge_branch", lambda path, branch: merges.append((path, branch)))
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda path, message: None)
    monkeypatch.setattr(cli, "rev_parse", lambda path, revision: "landed123")
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: removed.append(path))
    monkeypatch.setattr(beads, "close", lambda task_id, note: beads.closed.append((task_id, note)))

    cli._land_record(tmp_path, beads, child, cleanup_done=True, close_note="landed")

    assert merges == []
    assert removed == []


def test_land_all_uses_oldest_first_and_continues_after_conflict(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    calls: list[tuple[str, object]] = []
    for task_id, started_at in (
        ("bd-new", "2026-06-03T00:00:00+00:00"),
        ("bd-old", "2026-06-01T00:00:00+00:00"),
        ("bd-middle", "2026-06-02T00:00:00+00:00"),
    ):
        beads.items[task_id] = BeadSummary(
            id=task_id,
            title=task_id,
            labels=("flow", "reviewing", "reviewed"),
        )
        run_dir = tmp_path / ".flow" / "runs" / task_id
        RunRecord(
            task_id=task_id,
            branch=f"c3x/{task_id}",
            worktree=str(tmp_path / ".flow" / "worktrees" / task_id),
            prompt=str(run_dir / "prompt.md"),
            result=str(run_dir / "result.json"),
            last_message=str(run_dir / "last-message.md"),
            status="reviewed",
            started_at=started_at,
        ).save(run_dir / "run.json")

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "worktree_has_changes", lambda root, ignored_prefixes=(): False)
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: None)
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda root, message: None)
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: f"landed-{revision}")
    monkeypatch.setattr(beads, "close", lambda task_id, note: calls.append(("close", task_id)))

    def merge(root: Path, branch: str) -> None:
        calls.append(("merge", branch))
        if branch == "c3x/bd-old":
            raise cli.GitMergeConflict(branch, ["shared.py"], "content conflict")

    monkeypatch.setattr(cli, "merge_branch", merge)

    result = runner.invoke(cli.app, ["land", "--all", "--no-cleanup"])

    assert result.exit_code == 1
    assert [value for name, value in calls if name == "merge"] == [
        "c3x/bd-old",
        "c3x/bd-middle",
        "c3x/bd-new",
    ]
    assert ("close", "bd-middle") in calls
    assert ("close", "bd-new") in calls
    assert "Landed 2; blocked 1" in result.stdout
    assert ("bd-old", ["flow", "blocked", "land-blocked", "blocker-merge-conflict"]) in beads.added_labels


def test_order_land_records_puts_dependencies_before_older_dependents(tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.blockers.append(("bd-new", "bd-old"))
    paths = {
        "worktree": str(tmp_path),
        "prompt": str(tmp_path / "prompt.md"),
        "result": str(tmp_path / "result.json"),
        "last_message": str(tmp_path / "last-message.md"),
    }
    old = RunRecord(task_id="bd-old", branch="old", started_at="2026-06-01T00:00:00+00:00", **paths)
    new = RunRecord(task_id="bd-new", branch="new", started_at="2026-06-02T00:00:00+00:00", **paths)

    ordered = cli._order_land_records(beads, [old, new])

    assert [record.task_id for record in ordered] == ["bd-new", "bd-old"]


def test_review_commits_worktree_before_running_reviewer(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "reviewing"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    (run_dir / "result.json").parent.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    ).save(run_dir / "run.json")
    calls: list[tuple[str, object]] = []

    def fake_run_reviewer(*args: object, **kwargs: object) -> ReviewResult:
        calls.append(("review", kwargs["diff_summary"]))
        return ReviewResult(task_id="bd-1", status="approved", summary="ok")

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: calls.append(("commit", path)))
    monkeypatch.setattr(cli, "branch_diff_summary", lambda root, branch: calls.append(("diff", branch)) or "diff")
    monkeypatch.setattr(cli, "run_reviewer", fake_run_reviewer)

    result = runner.invoke(cli.app, ["review", "bd-1"])

    assert result.exit_code == 0
    assert calls[:3] == [("commit", worktree), ("diff", "c3x/bd-1-fix"), ("review", "diff")]


def test_auto_review_commits_worktree_before_running_reviewer(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "reviewing"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    ).save(run_dir / "run.json")
    calls: list[tuple[str, object]] = []

    def fake_run_reviewer(*args: object, **kwargs: object) -> ReviewResult:
        calls.append(("review", kwargs["diff_summary"]))
        return ReviewResult(task_id="bd-1", status="approved", summary="ok")

    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: calls.append(("commit", path)))
    monkeypatch.setattr(cli, "branch_diff_summary", lambda root, branch: calls.append(("diff", branch)) or "diff")
    monkeypatch.setattr(cli, "run_reviewer", fake_run_reviewer)

    cli._auto_review(tmp_path, beads)

    assert calls[:3] == [("commit", worktree), ("diff", "c3x/bd-1-fix"), ("review", "diff")]


def test_auto_review_defers_when_reviewer_exits_with_error(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "reviewing"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    ).save(run_dir / "run.json")

    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: None)
    monkeypatch.setattr(cli, "branch_diff_summary", lambda root, branch: "diff")
    monkeypatch.setattr(
        cli,
        "run_reviewer",
        lambda *args, **kwargs: (_ for _ in ()).throw(cli.AgentError("reviewer exited with exit code 1")),
    )

    cli._auto_review(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "completed"
    assert set(beads.items) == {"bd-1"}
    assert beads.blockers == []
    assert "reviewing" in beads.items["bd-1"].labels
    assert "reviewed" not in beads.items["bd-1"].labels
    assert "blocked" not in beads.items["bd-1"].labels
    assert beads.notes == [("bd-1", "c3x auto-review deferred: reviewer exited with exit code 1")]


def test_auto_review_blocks_when_record_worktree_is_missing(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "reviewing"))
    beads.next_id = 2
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    ).save(run_dir / "run.json")

    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(
        cli,
        "run_reviewer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reviewer should not run")),
    )

    cli._auto_review(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    cleanup_ids = [item_id for item_id in beads.items if item_id != "bd-1"]
    assert saved.status == "blocked"
    assert saved.outcome == "review-blocked"
    assert cleanup_ids
    assert beads.items[cleanup_ids[0]].title == "Fix review issue for bd-1: Resolve auto-review blocker"
    assert f"worker worktree is missing: {worktree}" in (beads.items[cleanup_ids[0]].description or "")
    assert beads.blockers == [(cleanup_ids[0], "bd-1")]
    assert ("bd-1", ["flow", "blocked", "review-blocked", "blocker-review-issues"]) in beads.added_labels
    assert ("bd-1", ["running", "reviewing", "reviewed"]) in beads.removed_labels


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


def test_inbox_uses_same_active_items_as_status(monkeypatch, tmp_path: Path) -> None:
    class ActiveOnlyBeads(_RecordingBeads):
        def list_open(self) -> list[BeadSummary]:
            raise AssertionError("inbox should use list_active")

    runner = CliRunner()
    beads = ActiveOnlyBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="triage me", labels=("flow", "inbox", "idea"))
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["inbox"])

    assert result.exit_code == 0
    assert "bd-1" in result.stdout
    assert "triage me" in result.stdout


def test_status_renders_workflow_counts(monkeypatch, tmp_path: Path) -> None:
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
    assert "c3x supervisor" in result.stdout
    assert "c3x workflow" in result.stdout
    assert "submitted" in result.stdout
    assert "questions" in result.stdout
    assert "max parallel workers" in result.stdout
    assert "land" in result.stdout
    assert "open c3x items" in result.stdout
    assert "3" in result.stdout


def test_status_avoids_deep_beads_and_unstick_scans(monkeypatch, tmp_path: Path) -> None:
    class FastStatusBeads(_RecordingBeads):
        def __init__(self) -> None:
            super().__init__()
            self.items["bd-ready"] = BeadSummary(id="bd-ready", title="queued", labels=("flow", "ready"))
            self.active_calls = 0

        def list_active(self) -> list[BeadSummary]:
            self.active_calls += 1
            return super().list_active()

        def ready(self) -> list[BeadSummary]:
            raise AssertionError("status should not run bd ready")

    runner = CliRunner()
    beads = FastStatusBeads()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )
    monkeypatch.setattr(
        cli,
        "_unstick_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("status should not run deep unstick")),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert beads.active_calls == 1
    assert "queued" in result.stdout


def test_status_reviewing_count_excludes_reviewed_and_land_blocked(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-review"] = BeadSummary(id="bd-review", title="needs review", labels=("flow", "reviewing"))
    beads.items["bd-land"] = BeadSummary(
        id="bd-land",
        title="ready to land",
        labels=("flow", "reviewing", "reviewed"),
    )
    beads.items["bd-blocked"] = BeadSummary(
        id="bd-blocked",
        title="land blocked",
        labels=("flow", "reviewing", "reviewed", "blocked", "land-blocked"),
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "review" in result.stdout
    assert "land" in result.stdout
    assert cli._reviewing_items(list(beads.items.values())) == [beads.items["bd-review"]]
    assert cli._ready_to_land_items(list(beads.items.values())) == [beads.items["bd-land"]]


def test_workflow_rows_classify_every_active_flow_item_once(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-inbox"] = BeadSummary(id="bd-inbox", title="idea", labels=("flow", "inbox", "idea"))
    beads.items["bd-question"] = BeadSummary(
        id="bd-question",
        title="question",
        labels=("flow", "question", "needs-human-clarification"),
    )
    beads.items["bd-ready"] = BeadSummary(id="bd-ready", title="queued", labels=("flow", "ready"))
    beads.items["bd-review"] = BeadSummary(id="bd-review", title="review", labels=("flow", "reviewing"))
    beads.items["bd-land"] = BeadSummary(id="bd-land", title="land", labels=("flow", "reviewing", "reviewed"))
    beads.items["bd-blocked"] = BeadSummary(id="bd-blocked", title="blocked", labels=("flow", "blocked"))
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 2})()})(),
    )

    rows = cli._workflow_rows(tmp_path, beads.list_active(), beads.ready())

    total_row = rows[-1]
    state_rows = [row for row in rows if row.state not in {"total", "capacity"}]
    assert total_row.count == 6
    assert sum(row.count for row in state_rows) == total_row.count
    queued = next(row for row in rows if row.stage == "queued")
    assert queued.count == 1
    assert "worker slots available" in queued.detail


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
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: pid == 1234)
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


def test_status_renders_captured_codex_status(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _StatusBeads()
    run_dir = tmp_path / ".flow" / "runs" / "bd-3"
    last_message = run_dir / "last-message.md"
    last_message.parent.mkdir(parents=True)
    last_message.write_text(
        "/status\n"
        "Model: gpt-5.4-mini\n"
        "Context: 12k / 50k\n"
        "\n"
        "continuing task",
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-3",
        branch="c3x/bd-3-running",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-3-running"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(last_message),
        pid=1234,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: pid == 1234)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "codex /status" in result.stdout
    assert "Model: gpt-5.4-mini Context: 12k / 50k" in result.stdout


def test_status_renders_usage_limit_fallback_for_blocked_worker(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-usage"] = BeadSummary(id="bd-usage", title="blocked", labels=("flow", "blocked"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-usage"
    stderr = run_dir / "stderr.log"
    stderr.parent.mkdir(parents=True)
    stderr.write_text("ERROR: You've hit your usage limit. Try again later.", encoding="utf-8")
    RunRecord(
        task_id="bd-usage",
        branch="c3x/bd-usage",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-usage"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        pid=1234,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "bd-usage" in result.stdout
    assert "Codex usage limit evidence" in result.stdout


def test_status_hides_provider_logs_for_closed_records(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    run_dir = tmp_path / ".flow" / "runs" / "bd-closed"
    stderr = run_dir / "stderr.log"
    stderr.parent.mkdir(parents=True)
    stderr.write_text("ERROR: You've hit your usage limit. Try again later.", encoding="utf-8")
    RunRecord(
        task_id="bd-closed",
        branch="c3x/bd-closed",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-closed"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        pid=1234,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "bd-closed" not in result.stdout
    assert "no captured /status output" in result.stdout


def test_status_renders_captured_antigravity_status(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _StatusBeads()
    run_dir = tmp_path / ".flow" / "runs" / "bd-3"
    last_message = run_dir / "last-message.md"
    last_message.parent.mkdir(parents=True)
    last_message.write_text(
        "antigravity /status\n"
        "Model: gpt-5.4-mini\n"
        "Context: 12k / 50k\n"
        "\n"
        "working hard",
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-3",
        branch="c3x/bd-3-running",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-3-running"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(last_message),
        pid=1234,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: pid == 1234)
    
    class MockLimits:
        max_parallel_workers = 3
    class MockAgents:
        provider = "antigravity"
    class MockConfig:
        limits = MockLimits()
        agents = MockAgents()

    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: MockConfig(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "antigravity /status" in result.stdout
    assert "Model: gpt-5.4-mini Context: 12k / 50k" in result.stdout


def test_status_live_uses_alternate_screen() -> None:
    live = cli._status_live(cli._build_activity_table(Path("/tmp")))

    assert live._screen is True


def test_status_workers_table_hides_dead_and_non_running_records(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _StatusBeads()
    live_dir = tmp_path / ".flow" / "runs" / "bd-live"
    dead_dir = tmp_path / ".flow" / "runs" / "bd-dead"
    reviewed_dir = tmp_path / ".flow" / "runs" / "bd-reviewed"
    RunRecord(
        task_id="bd-live",
        branch="c3x/bd-live",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-live"),
        prompt=str(live_dir / "prompt.md"),
        result=str(live_dir / "result.json"),
        last_message=str(live_dir / "last-message.md"),
        pid=111,
        status="running",
    ).save(live_dir / "run.json")
    RunRecord(
        task_id="bd-dead",
        branch="c3x/bd-dead",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-dead"),
        prompt=str(dead_dir / "prompt.md"),
        result=str(dead_dir / "result.json"),
        last_message=str(dead_dir / "last-message.md"),
        pid=222,
        status="running",
    ).save(dead_dir / "run.json")
    RunRecord(
        task_id="bd-reviewed",
        branch="c3x/bd-reviewed",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-reviewed"),
        prompt=str(reviewed_dir / "prompt.md"),
        result=str(reviewed_dir / "result.json"),
        last_message=str(reviewed_dir / "last-message.md"),
        pid=333,
        status="reviewed",
    ).save(reviewed_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: pid == 111)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda root: type("Config", (), {"limits": type("Limits", (), {"max_parallel_workers": 3})()})(),
    )

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "bd-live" in result.stdout
    assert "111" in result.stdout
    assert "bd-dead" not in result.stdout
    assert "222" not in result.stdout
    assert "bd-reviewed" not in result.stdout
    assert "333" not in result.stdout


def test_live_worker_records_only_returns_running_live_pids(monkeypatch, tmp_path: Path) -> None:
    live_dir = tmp_path / ".flow" / "runs" / "bd-live"
    dead_dir = tmp_path / ".flow" / "runs" / "bd-dead"
    reviewed_dir = tmp_path / ".flow" / "runs" / "bd-reviewed"
    RunRecord(
        task_id="bd-live",
        branch="c3x/bd-live",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-live"),
        prompt=str(live_dir / "prompt.md"),
        result=str(live_dir / "result.json"),
        last_message=str(live_dir / "last-message.md"),
        pid=111,
        status="running",
    ).save(live_dir / "run.json")
    RunRecord(
        task_id="bd-dead",
        branch="c3x/bd-dead",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-dead"),
        prompt=str(dead_dir / "prompt.md"),
        result=str(dead_dir / "result.json"),
        last_message=str(dead_dir / "last-message.md"),
        pid=222,
        status="running",
    ).save(dead_dir / "run.json")
    RunRecord(
        task_id="bd-reviewed",
        branch="c3x/bd-reviewed",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-reviewed"),
        prompt=str(reviewed_dir / "prompt.md"),
        result=str(reviewed_dir / "result.json"),
        last_message=str(reviewed_dir / "last-message.md"),
        pid=333,
        status="reviewed",
    ).save(reviewed_dir / "run.json")
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: pid == 111)

    records = cli._live_worker_records(tmp_path)

    assert [record.task_id for record in records] == ["bd-live"]


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


def test_blocked_note_reason_detects_usage_limit_by_simple_grep() -> None:
    reason = cli._blocked_note_reason(
        "ERROR: Usage limit reached. Visit https://chatgpt.com/codex/settings/usage for credits."
    )

    assert reason == "Codex usage limit; worker exited without result.json"


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
    assert activity["supervisor"] == (
        "tick complete; dispatch disabled; use c3x run --dispatch or c3x watch to start workers"
    )
    events = cli._activity_events(activity)
    assert any(event["event"] == "checking critic tasks" and event["detail"] == "critic tasks OK" for event in events)


def test_supervisor_tick_continues_when_conflict_resolver_candidate_is_already_landed(
    monkeypatch, tmp_path: Path
) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="stale conflict", labels=("flow", "landed"))
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "_import_finished_results", lambda root, beads: None)
    monkeypatch.setattr(cli, "_plan_inbox", lambda root, beads: None)
    monkeypatch.setattr(cli, "_maybe_warn_stuck", lambda root, beads: None)
    monkeypatch.setattr(cli, "_supervisor_idle_reason", lambda root, beads, dispatch: "idle")
    monkeypatch.setattr(cli, "_conflict_task_ids", lambda root, beads, task_id, all_tasks: ["bd-1"])
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(
        cli,
        "_resolve_conflict_task",
        lambda root, config, beads, task_id: (_ for _ in ()).throw(ValueError(f"{task_id} is already landed")),
    )

    cli._supervisor_tick(tmp_path, dispatch=False, resolve_conflicts=True)

    assert ("bd-1", "c3x conflict resolver blocked: bd-1 is already landed") in beads.notes


def test_critic_tick_reports_blocked_tasks_without_creating_task() -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="one", labels=("flow", "blocked"))
    beads.items["bd-2"] = BeadSummary(id="bd-2", title="two", labels=("flow", "blocked"))

    result = cli._critic_tick(beads)

    assert result == "2 blocked tasks; run c3x critic to create an investigation task"
    assert set(beads.items) == {"bd-1", "bd-2"}


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
    assert any("Worker produced a final message" in note for _, note in beads.notes)
    assert any("last_message_path:" in note for _, note in beads.notes)


def test_import_uses_result_path_reported_in_last_message(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    actual_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"
    actual_result = actual_worktree / ".c3x" / "bd-1-result.json"
    actual_result.parent.mkdir(parents=True)
    actual_result.write_text(
        WorkerResult(
            task_id="bd-1",
            status="completed",
            summary="Fixed it",
            task_kind="bug",
            confidence="high",
        ).model_dump_json(),
        encoding="utf-8",
    )
    run_dir.mkdir(parents=True)
    last_message = run_dir / "last-message.md"
    last_message.write_text(
        f"Result written to [`.c3x/bd-1-result.json`]({actual_result}).\n",
        encoding="utf-8",
    )
    stale_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(stale_worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(stale_worktree / ".c3x" / "result.json"),
        last_message=str(last_message),
        pid=12345,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)

    cli._import_finished_results(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "completed"
    assert saved.result == str(actual_result)
    assert saved.worktree == str(actual_worktree)
    assert ("bd-1", ["flow", "reviewing", "completed-by-agent"]) in beads.added_labels


def test_import_ignores_archived_running_records(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "reviewing", "reviewed"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
    run_dir.mkdir(parents=True)
    archived_dir.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
        attempt=2,
    ).save(run_dir / "run.json")
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(archived_dir / "prompt.md"),
        result=str(archived_dir / "result.json"),
        last_message=str(archived_dir / "last-message.md"),
        pid=12345,
        attempt=1,
    ).save(archived_dir / "run.json")
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)

    cli._import_finished_results(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "reviewed"
    assert not beads.added_labels


def test_land_repairs_current_record_from_archived_completed_result(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "reviewing", "reviewed"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-2"
    stale_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"
    actual_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"
    actual_result = actual_worktree / ".c3x" / "result.json"
    actual_result.parent.mkdir(parents=True)
    actual_result.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="Fixed it").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(stale_worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(stale_worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="reviewed",
        attempt=2,
    ).save(run_dir / "run.json")
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(stale_worktree),
        prompt=str(archived_dir / "prompt.md"),
        result=str(stale_worktree / ".c3x" / "result.json"),
        last_message=str(archived_dir / "last-message.md"),
        status="blocked",
        attempt=2,
    ).save(archived_dir / "run.json")
    (archived_dir / "last-message.md").write_text(
        f"Result written to [`.c3x/result.json`]({actual_result}).\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(beads, "close", lambda task_id, note: beads.closed.append((task_id, note)))
    monkeypatch.setattr(
        cli,
        "current_branch",
        lambda worktree: "feature" if worktree == tmp_path else "c3x/bd-1-fix-attempt-3",
    )
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: calls.append(("commit", path)))
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: calls.append(("merge", branch)))
    monkeypatch.setattr(cli, "commit_ledger_changes", lambda root, message: None)
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: "landed123")
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: None)
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: None)

    result = runner.invoke(cli.app, ["land", "bd-1"])

    assert result.exit_code == 0
    assert ("commit", actual_worktree) in calls
    assert ("merge", "c3x/bd-1-fix-attempt-3") in calls
    saved = RunRecord.load(run_dir / "run.json")
    assert saved.attempt == 3
    assert saved.worktree == str(actual_worktree)
    assert saved.branch == "c3x/bd-1-fix-attempt-3"


def test_review_does_not_use_older_archived_result_for_newer_attempt(tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    current_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-conflict-attempt-5"
    archived_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"
    archived_result = archived_worktree / ".c3x" / "result.json"
    archived_result.parent.mkdir(parents=True)
    archived_result.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="Old result").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-conflict-attempt-5",
        worktree=str(current_worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(current_worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="running",
        attempt=5,
    ).save(run_dir / "run.json")
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-3"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-3",
        worktree=str(archived_worktree),
        prompt=str(archived_dir / "prompt.md"),
        result=str(archived_result),
        last_message=str(archived_dir / "last-message.md"),
        status="completed",
        attempt=3,
    ).save(archived_dir / "run.json")

    try:
        cli._load_worker_result(tmp_path, "bd-1")
    except ValueError as exc:
        assert "missing worker result" in str(exc)
    else:
        raise AssertionError("expected missing worker result")


def test_review_saves_blocked_result_when_final_message_exists_without_result(tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-conflict-attempt-5"
    worktree.mkdir(parents=True)
    conflicted = worktree / "app.py"
    conflicted.write_text("<<<<<<< HEAD\nleft\n=======\nright\n>>>>>>> branch\n", encoding="utf-8")
    last_message = run_dir / "last-message.md"
    last_message.parent.mkdir(parents=True)
    last_message.write_text("Merge conflict resolved, but conflict markers remain in app.py.", encoding="utf-8")
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-conflict-attempt-5",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(last_message),
        status="running",
        attempt=5,
    ).save(run_dir / "run.json")

    result = cli._load_worker_result(tmp_path, "bd-1")

    assert result.status == "blocked"
    assert result.blocker_category == "merge-conflict"
    assert any("result.json is missing" in blocker for blocker in result.blockers)
    assert (run_dir / "result.json").exists()


def test_missing_result_note_summarizes_logs_without_embedding_them(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    run_dir.mkdir(parents=True)
    last_message = run_dir / "last-message.md"
    stderr = run_dir / "stderr.log"
    last_message.write_text(
        "Wrote [`.c3x/result.json`](/tmp/wrong/.c3x/result.json).\n" * 80,
        encoding="utf-8",
    )
    stderr.write_text(
        "very long stderr line\n" * 1000,
        encoding="utf-8",
    )
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

    note = beads.notes[-1][1]
    assert "summary: Worker reported writing result.json, but not at the expected path." in note
    assert "last_message_path:" in note
    assert "stderr_path:" in note
    assert "very long stderr line\nvery long stderr line" not in note
    assert len(note) < 1000


def test_missing_result_summary_detects_usage_limit_in_stderr(tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    run_dir.mkdir(parents=True)
    last_message = run_dir / "last-message.md"
    stderr = run_dir / "stderr.log"
    stderr.write_text(
        "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
        "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 6:27 PM.\n",
        encoding="utf-8",
    )
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(last_message),
        pid=12345,
    )

    summary = cli._missing_result_summary(record, last_message_path=last_message, stderr_path=stderr)

    assert summary == "Codex usage limit stopped the worker before c3x found result.json."


def test_missing_result_beads_write_failure_does_not_abort_import(monkeypatch, tmp_path: Path) -> None:
    class FailingBeads(_RecordingBeads):
        def add_note(self, task_id: str, note: str) -> None:
            raise cli.BeadsError("old_value is too large")

        def add_labels(self, task_id: str, labels: list[str]) -> None:
            raise cli.BeadsError("old_value is too large")

        def remove_labels(self, task_id: str, labels: list[str]) -> None:
            raise cli.BeadsError("old_value is too large")

    beads = FailingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    run_dir.mkdir(parents=True)
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        pid=12345,
    )
    record.save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)

    cli._import_finished_results(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "blocked"
    assert saved.outcome == "missing-result"


def test_beads_error_summary_omits_large_rejected_payload() -> None:
    summary = cli._beads_error_summary(
        cli.BeadsError("failed: Error 1105: string '{\"notes\":\"very long\"}' is too large for column 'old_value'")
    )

    assert summary == "Beads rejected the update because the existing issue payload is too large for its event log."
    assert "very long" not in summary


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


def test_import_completed_result_survives_beads_write_failure(tmp_path: Path) -> None:
    class FailingBeads(_RecordingBeads):
        def add_note(self, task_id: str, note: str) -> None:
            raise cli.BeadsError("old_value is too large")

        def add_labels(self, task_id: str, labels: list[str]) -> None:
            raise cli.BeadsError("old_value is too large")

        def remove_labels(self, task_id: str, labels: list[str]) -> None:
            raise cli.BeadsError("old_value is too large")

    beads = FailingBeads()
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

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "completed"
    assert saved.outcome == "completed"
    assert (run_dir / "result.json").exists()


def test_apply_review_result_creates_blocking_cleanup_tasks(tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix auth",
        description="Preserve redirect params.",
        labels=("flow", "reviewing"),
    )
    beads.next_id = 2
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    record = RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-auth",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-auth"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    )
    worker_result = WorkerResult(task_id="bd-1", status="completed", summary="Changed redirect handling")
    review_result = ReviewResult.model_validate(
        {
            "task_id": "bd-1",
            "status": "blocked",
            "summary": "Acceptance not met",
            "requirements": [
                {
                    "requirement": "Preserve redirect params",
                    "status": "unmet",
                    "evidence": "No regression test",
                }
            ],
            "issues": [
                {
                    "title": "Add regression test for redirect params",
                    "description": "Test and fix missing redirect query preservation.",
                    "severity": "high",
                }
            ],
        }
    )

    cli._apply_review_result(
        tmp_path,
        beads,
        beads.items["bd-1"],
        worker_result,
        review_result,
        record=record,
    )

    saved = RunRecord.load(run_dir / "run.json")
    cleanup_ids = [item_id for item_id in beads.items if item_id != "bd-1"]
    assert saved.status == "blocked"
    assert saved.outcome == "review-blocked"
    assert cleanup_ids
    assert beads.items[cleanup_ids[0]].priority == 0
    assert beads.blockers == [(cleanup_ids[0], "bd-1")]
    assert ("bd-1", ["flow", "blocked", "review-blocked", "blocker-review-issues"]) in beads.added_labels


def test_apply_review_result_does_not_create_nested_review_fix(tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="fix review issue",
        labels=("flow", "reviewing", "review-fix"),
    )
    beads.next_id = 3
    run_dir = tmp_path / ".flow" / "runs" / "bd-2"
    record = RunRecord(
        task_id="bd-2",
        branch="c3x/bd-2-fix-review-issue",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-2-fix-review-issue"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    )
    worker_result = WorkerResult(task_id="bd-2", status="completed", summary="Tried cleanup")
    review_result = ReviewResult(
        task_id="bd-2",
        status="blocked",
        summary="Cleanup still incomplete",
        issues=[
            cli.ReviewIssue(
                title="Finish cleanup",
                description="The original issue remains.",
                severity="high",
            )
        ],
    )

    cli._apply_review_result(
        tmp_path,
        beads,
        beads.items["bd-2"],
        worker_result,
        review_result,
        record=record,
    )

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "blocked"
    assert saved.outcome == "review-blocked"
    assert list(beads.items) == ["bd-2"]
    assert beads.blockers == []
    assert ("bd-2", ["flow", "blocked", "review-blocked", "blocker-review-issues"]) in beads.added_labels


def test_recover_interrupted_worker_resumes_transient_session(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", labels=("flow", "running"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        pid=12345,
        status="running",
        attempt=1,
    ).save(run_dir / "run.json")
    (run_dir / "stderr.log").write_text(
        "session id: 019e61af-8603-7b53-8099-9284e6bc16bd\n"
        "ERROR: You've hit your usage limit. Try again later.\n",
        encoding="utf-8",
    )

    def fake_resume_session_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        session_id: str,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=previous.result,
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            pid=67890,
            attempt=attempt or 2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)
    monkeypatch.setattr(cli, "resume_session_worker", fake_resume_session_worker)

    cli._recover_interrupted_workers(tmp_path, beads)

    saved = RunRecord.load(run_dir / "run.json")
    assert saved.attempt == 2
    assert saved.pid == 67890
    assert (tmp_path / ".flow" / "runs" / "bd-1-attempt-2" / "run.json").exists()
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


def test_retry_fresh_archives_current_run_and_starts_new_worktree(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="blocked",
        labels=("flow", "blocked", "blocker-result-missing"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.md").write_text("prompt", encoding="utf-8")
    (run_dir / "last-message.md").write_text("last", encoding="utf-8")
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

    def fake_start_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        attempt: int | None = None,
    ) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-attempt-2",
            worktree=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1", "--fresh"])

    assert result.exit_code == 0
    archived = tmp_path / ".flow" / "runs" / "bd-1-attempt-2"
    archived_record = RunRecord.load(archived / "run.json")
    assert archived_record.prompt == str(archived / "prompt.md")
    assert archived_record.last_message == str(archived / "last-message.md")
    assert RunRecord.load(run_dir / "run.json").attempt == 2
    assert ("bd-1", "open") in beads.statuses
    assert ("bd-1", "in_progress") in beads.statuses
    assert ("bd-1", ["flow", "running", "attempt-2"]) in beads.added_labels
    assert any("blocker-result-missing" in labels for item_id, labels in beads.removed_labels if item_id == "bd-1")


def test_retry_archive_dir_uses_new_attempt_number(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", status="blocked", labels=("flow", "blocked"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-3",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        attempt=3,
    ).save(run_dir / "run.json")
    (tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-5").mkdir(parents=True)

    def fake_start_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        attempt: int | None = None,
    ) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch=f"c3x/bd-1-fix-attempt-{attempt}",
            worktree=str(root / ".flow" / "worktrees" / f"c3x-bd-1-fix-attempt-{attempt}"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / f"c3x-bd-1-fix-attempt-{attempt}" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 1,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1", "--fresh"])

    assert result.exit_code == 0
    assert (tmp_path / ".flow" / "runs" / "bd-1-attempt-6" / "run.json").exists()
    assert not (tmp_path / ".flow" / "runs" / "bd-1-attempt-3-2").exists()
    assert RunRecord.load(run_dir / "run.json").attempt == 6
    assert ("bd-1", ["flow", "running", "attempt-6"]) in beads.added_labels


def test_retry_defaults_to_resuming_previous_session(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="blocked",
        labels=("flow", "blocked", "blocker-result-missing"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(run_dir / "run.json")
    (run_dir / "stderr.log").write_text(
        "session id: 019e61af-8603-7b53-8099-9284e6bc16bd\n",
        encoding="utf-8",
    )
    resumed: list[str] = []

    def fake_resume_session_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        session_id: str,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        resumed.append(session_id)
        record = RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=previous.result,
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "resume_session_worker", fake_resume_session_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1"])

    assert result.exit_code == 0
    assert "Resumed session" in result.stdout
    assert resumed == ["019e61af-8603-7b53-8099-9284e6bc16bd"]
    assert RunRecord.load(run_dir / "run.json").attempt == 2
    assert ("bd-1", ["flow", "running", "attempt-2"]) in beads.added_labels


def test_retry_clears_review_cleanup_blockers(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="blocked",
        labels=("flow", "blocked", "review-blocked", "blocker-review-issues"),
    )
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Fix review issue for bd-1: add test",
        description="Blocks: bd-1\n\nAdd missing test.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.blockers.append(("bd-2", "bd-1"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(run_dir / "run.json")

    def fake_start_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        attempt: int | None = None,
    ) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-attempt-2",
            worktree=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1", "--fresh"])

    assert result.exit_code == 0
    assert ("bd-2", "bd-1") in beads.removed_blockers
    assert ("bd-2", "Superseded by retry of bd-1") in beads.closed
    assert any("cleared 1 superseded review cleanup" in note for item_id, note in beads.notes if item_id == "bd-1")


def test_retry_clears_nested_review_cleanup_blockers(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="blocked",
        labels=("flow", "blocked", "review-blocked", "blocker-review-issues"),
    )
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Fix review issue for bd-1",
        description="Blocks: bd-1\n\nFirst cleanup.",
        labels=("flow", "blocked", "review-fix"),
    )
    beads.items["bd-3"] = BeadSummary(
        id="bd-3",
        title="Fix review issue for bd-2",
        description="Blocks: bd-2\n\nNested cleanup.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.blockers.extend([("bd-2", "bd-1"), ("bd-3", "bd-2")])
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(run_dir / "run.json")

    def fake_start_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        attempt: int | None = None,
    ) -> RunRecord:
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-attempt-2",
            worktree=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1", "--fresh"])

    assert result.exit_code == 0
    assert beads.removed_blockers == [("bd-3", "bd-2"), ("bd-2", "bd-1")]
    assert ("bd-2", "Superseded by retry of bd-1") in beads.closed
    assert ("bd-3", "Superseded by retry of bd-1") in beads.closed
    assert any("cleared 2 superseded review cleanup" in note for item_id, note in beads.notes if item_id == "bd-1")
    assert "bd-2" not in beads.items
    assert "bd-3" not in beads.items


def test_retry_review_fix_fresh_uses_source_worktree(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="original",
        status="blocked",
        labels=("flow", "blocked", "review-blocked"),
    )
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Fix review issue for bd-1: add test",
        description="Blocks: bd-1\n\nAdd missing test.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.blockers.append(("bd-2", "bd-1"))
    parent_run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    source_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    source_worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(source_worktree),
        prompt=str(parent_run_dir / "prompt.md"),
        result=str(source_worktree / ".c3x" / "result.json"),
        last_message=str(parent_run_dir / "last-message.md"),
        status="blocked",
        attempt=3,
    ).save(parent_run_dir / "run.json")
    continued: list[str] = []
    started: list[str] = []

    def fake_continue_worktree_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        continued.append(previous.worktree)
        record = RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(Path(previous.worktree) / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 1,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    def fake_start_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        attempt: int | None = None,
    ) -> RunRecord:
        started.append(task.id)
        raise AssertionError("review-fix retry must not create a new worktree")

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(
        cli,
        "current_branch",
        lambda root: "c3x/bd-1-fix" if root == source_worktree else "feature",
    )
    monkeypatch.setattr(cli, "continue_worktree_worker", fake_continue_worktree_worker)
    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    result = runner.invoke(cli.app, ["retry", "bd-2", "--fresh"])

    assert result.exit_code == 0
    assert "Continued worktree" in result.stdout
    assert continued == [str(source_worktree)]
    assert started == []
    saved = RunRecord.load(tmp_path / ".flow" / "runs" / "bd-2" / "run.json")
    assert saved.branch == "c3x/bd-1-fix"
    assert saved.worktree == str(source_worktree)


def test_dispatch_review_fix_uses_source_worktree(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    parent = BeadSummary(id="bd-1", title="original", labels=("flow", "blocked", "review-blocked"))
    cleanup = BeadSummary(
        id="bd-2",
        title="Fix review issue for bd-1: add test",
        description="Blocks: bd-1\n\nAdd missing test.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.items[parent.id] = parent
    beads.items[cleanup.id] = cleanup
    parent_run_dir = tmp_path / ".flow" / "runs" / parent.id
    source_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    source_worktree.mkdir(parents=True)
    RunRecord(
        task_id=parent.id,
        branch="c3x/bd-1-fix",
        worktree=str(source_worktree),
        prompt=str(parent_run_dir / "prompt.md"),
        result=str(source_worktree / ".c3x" / "result.json"),
        last_message=str(parent_run_dir / "last-message.md"),
        status="blocked",
        attempt=2,
    ).save(parent_run_dir / "run.json")
    continued: list[tuple[str, str]] = []

    def fake_continue_worktree_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        continued.append((task.id, previous.worktree))
        return RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(Path(previous.worktree) / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 1,
        )

    monkeypatch.setattr(cli, "continue_worktree_worker", fake_continue_worktree_worker)
    monkeypatch.setattr(
        cli,
        "start_worker",
        lambda root, config, task: (_ for _ in ()).throw(AssertionError("must use source worktree")),
    )

    record = cli._start_ready_worker(tmp_path, object(), beads, cleanup)

    assert continued == [(cleanup.id, str(source_worktree))]
    assert record.task_id == cleanup.id
    assert record.branch == "c3x/bd-1-fix"
    assert record.worktree == str(source_worktree)


def test_retry_nested_review_fix_fresh_uses_original_ancestor_worktree(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="original",
        labels=("flow", "blocked", "review-blocked"),
    )
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Fix first review issue",
        description="Blocks: bd-1\n\nFirst cleanup.",
        labels=("flow", "blocked", "review-fix"),
    )
    beads.items["bd-3"] = BeadSummary(
        id="bd-3",
        title="Fix nested review issue",
        description="Blocks: bd-2\n\nNested cleanup.",
        labels=("flow", "ready", "review-fix"),
    )
    original_run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    original_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    nested_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-2-fix"
    original_worktree.mkdir(parents=True)
    nested_worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(original_worktree),
        prompt=str(original_run_dir / "prompt.md"),
        result=str(original_worktree / ".c3x" / "result.json"),
        last_message=str(original_run_dir / "last-message.md"),
        status="blocked",
    ).save(original_run_dir / "run.json")
    RunRecord(
        task_id="bd-2",
        branch="c3x/bd-1-fix",
        worktree=str(nested_worktree),
        prompt=str(tmp_path / ".flow" / "runs" / "bd-2" / "prompt.md"),
        result=str(nested_worktree / ".c3x" / "result.json"),
        last_message=str(tmp_path / ".flow" / "runs" / "bd-2" / "last-message.md"),
        status="blocked",
    ).save(tmp_path / ".flow" / "runs" / "bd-2" / "run.json")
    continued: list[str] = []

    def fake_continue_worktree_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        continued.append(previous.worktree)
        record = RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(Path(previous.worktree) / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 1,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "c3x/bd-1-fix")
    monkeypatch.setattr(cli, "continue_worktree_worker", fake_continue_worktree_worker)
    monkeypatch.setattr(
        cli,
        "start_worker",
        lambda root, config, task, attempt=None: (_ for _ in ()).throw(AssertionError("must use ancestor worktree")),
    )

    result = runner.invoke(cli.app, ["retry", "bd-3", "--fresh"])

    assert result.exit_code == 0
    assert continued == [str(original_worktree)]
    saved = RunRecord.load(tmp_path / ".flow" / "runs" / "bd-3" / "run.json")
    assert saved.worktree == str(original_worktree)


def test_dispatch_nested_review_fix_uses_original_ancestor_worktree(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="original", labels=("flow", "blocked"))
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="first review fix",
        description="Blocks: bd-1\n\nFirst cleanup.",
        labels=("flow", "blocked", "review-fix"),
    )
    cleanup = BeadSummary(
        id="bd-3",
        title="nested review fix",
        description="Blocks: bd-2\n\nNested cleanup.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.items[cleanup.id] = cleanup
    original_run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    original_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    original_worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(original_worktree),
        prompt=str(original_run_dir / "prompt.md"),
        result=str(original_worktree / ".c3x" / "result.json"),
        last_message=str(original_run_dir / "last-message.md"),
        status="blocked",
    ).save(original_run_dir / "run.json")
    continued: list[tuple[str, str]] = []

    def fake_continue_worktree_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        continued.append((task.id, previous.worktree))
        return RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(Path(previous.worktree) / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 1,
        )

    monkeypatch.setattr(cli, "continue_worktree_worker", fake_continue_worktree_worker)

    record = cli._start_ready_worker(tmp_path, object(), beads, cleanup)

    assert continued == [("bd-3", str(original_worktree))]
    assert record.worktree == str(original_worktree)


def test_review_fix_cycle_falls_back_to_normal_worker(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    task = BeadSummary(
        id="bd-1",
        title="cycle",
        description="Blocks: bd-2\n\nCycle part one.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.items["bd-1"] = task
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="cycle",
        description="Blocks: bd-1\n\nCycle part two.",
        labels=("flow", "ready", "review-fix"),
    )
    started: list[str] = []

    def fake_start_worker(root: Path, config: object, task: BeadSummary) -> RunRecord:
        started.append(task.id)
        return RunRecord(
            task_id=task.id,
            branch="c3x/bd-1",
            worktree=str(root / ".flow" / "worktrees" / "bd-1"),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / "bd-1" / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
        )

    monkeypatch.setattr(cli, "start_worker", fake_start_worker)

    record = cli._start_ready_worker(tmp_path, object(), beads, task)

    assert started == ["bd-1"]
    assert record.task_id == "bd-1"


def test_retry_can_continue_existing_worktree_with_fresh_context(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="fix", status="blocked", labels=("flow", "blocked"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(worktree / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(run_dir / "run.json")
    continued: list[str] = []

    def fake_continue_worktree_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        previous: RunRecord,
        *,
        reason: str = "",
        attempt: int | None = None,
    ) -> RunRecord:
        continued.append(previous.worktree)
        record = RunRecord(
            task_id=task.id,
            branch=previous.branch,
            worktree=previous.worktree,
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=previous.result,
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 2,
        )
        record.save(root / ".flow" / "runs" / task.id / "run.json")
        return record

    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "load_config", lambda root: object())
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "continue_worktree_worker", fake_continue_worktree_worker)

    result = runner.invoke(cli.app, ["retry", "bd-1", "--continue-worktree"])

    assert result.exit_code == 0
    assert "Continued worktree" in result.stdout
    assert continued == [str(worktree)]


def test_retry_all_retries_blocked_flow_tasks(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="one", labels=("flow", "blocked"))
    beads.items["bd-2"] = BeadSummary(id="bd-2", title="two", labels=("flow", "blocked"))
    started: list[str] = []

    def fake_start_worker(
        root: Path,
        config: object,
        task: BeadSummary,
        *,
        attempt: int | None = None,
    ) -> RunRecord:
        started.append(task.id)
        return RunRecord(
            task_id=task.id,
            branch=f"c3x/{task.id}",
            worktree=str(root / ".flow" / "worktrees" / task.id),
            prompt=str(root / ".flow" / "runs" / task.id / "prompt.md"),
            result=str(root / ".flow" / "worktrees" / task.id / ".c3x" / "result.json"),
            last_message=str(root / ".flow" / "runs" / task.id / "last-message.md"),
            attempt=attempt or 1,
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
    removed_worktrees: list[tuple[Path, bool]] = []
    deleted_branches: list[tuple[str, bool]] = []
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
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: True)
    monkeypatch.setattr(
        cli,
        "remove_worktree",
        lambda root, worktree, force=False: removed_worktrees.append((worktree, force)),
    )
    monkeypatch.setattr(
        cli,
        "delete_branch",
        lambda root, branch, force=False: deleted_branches.append((branch, force)),
    )

    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    assert not archived_dir.exists()
    assert removed_worktrees == [(archived_worktree, True)]
    assert deleted_branches == [("c3x/bd-1-fix", True)]


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


def test_cleanup_removes_superseded_attempt_while_current_attempt_is_blocked(monkeypatch, tmp_path: Path) -> None:
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
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
        status="blocked",
        attempt=2,
    ).save(current_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})

    actions = cli._cleanup_actions(tmp_path, task_id=None)

    assert len(actions) == 1
    assert actions[0].worktree == archived_worktree
    assert actions[0].reason == "superseded attempt 1"
    assert actions[0].remove_run_dir


def test_cleanup_preserves_current_blocked_attempt(monkeypatch, tmp_path: Path) -> None:
    current_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
        prompt=str(current_dir / "prompt.md"),
        result=str(current_dir / "result.json"),
        last_message=str(current_dir / "last-message.md"),
        status="blocked",
        attempt=2,
    ).save(current_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})

    assert cli._cleanup_actions(tmp_path, task_id=None) == []


def test_cleanup_removes_closed_task_when_branch_is_contained(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {worktree: "c3x/bd-1-fix"})
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: True)

    actions = cli._cleanup_actions(tmp_path, task_id=None, closed_task_ids={"bd-1"})

    assert len(actions) == 1
    assert actions[0].reason == "closed task worktree"
    assert not actions[0].force_remove


def test_cleanup_preserves_closed_task_when_branch_is_unmerged(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {worktree: "c3x/bd-1-fix"})
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)

    assert cli._cleanup_actions(tmp_path, task_id=None, closed_task_ids={"bd-1"}) == []


def test_cleanup_removes_closed_task_worktree_when_branch_is_missing(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: False)

    actions = cli._cleanup_actions(tmp_path, task_id=None, closed_task_ids={"bd-1"})

    assert len(actions) == 1
    assert actions[0].reason == "closed task worktree with missing branch"


def test_cleanup_preserves_resources_shared_with_current_attempt(monkeypatch, tmp_path: Path) -> None:
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    for run_dir, attempt in ((archived_dir, 1), (tmp_path / ".flow" / "runs" / "bd-1", 2)):
        RunRecord(
            task_id="bd-1",
            branch="c3x/bd-1-fix",
            worktree=str(worktree),
            prompt=str(run_dir / "prompt.md"),
            result=str(run_dir / "result.json"),
            last_message=str(run_dir / "last-message.md"),
            status="blocked",
            attempt=attempt,
        ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})

    actions = cli._cleanup_actions(tmp_path, task_id=None)

    assert len(actions) == 1
    assert actions[0].preserve_worktree
    assert actions[0].preserve_branch


def test_cleanup_removes_unreferenced_managed_c3x_worktree(monkeypatch, tmp_path: Path) -> None:
    orphan = tmp_path / ".flow" / "worktrees" / "c3x-bd-orphan"
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {orphan: "c3x/bd-orphan"})

    actions = cli._cleanup_actions(tmp_path, task_id=None)

    assert len(actions) == 1
    assert actions[0].worktree == orphan
    assert actions[0].branch == "c3x/bd-orphan"
    assert actions[0].reason == "orphaned c3x worktree"
    assert actions[0].force_remove


def test_cleanup_preserves_unreferenced_worktree_outside_managed_directory(monkeypatch, tmp_path: Path) -> None:
    external = tmp_path / "manual-worktree"
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {external: "c3x/manual"})

    assert cli._cleanup_actions(tmp_path, task_id=None) == []


def test_cleanup_preserves_unreferenced_non_c3x_managed_worktree(monkeypatch, tmp_path: Path) -> None:
    managed = tmp_path / ".flow" / "worktrees" / "manual"
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {managed: "feature"})

    assert cli._cleanup_actions(tmp_path, task_id=None) == []


def test_cleanup_dry_run_reports_archived_run_metadata_repair(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    canonical_dir = tmp_path / ".flow" / "runs" / "bd-1"
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
    archived_dir.mkdir(parents=True)
    (archived_dir / "prompt.md").write_text("old prompt", encoding="utf-8")
    (archived_dir / "last-message.md").write_text("old last", encoding="utf-8")
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(canonical_dir / "prompt.md"),
        result=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix" / ".c3x" / "result.json"),
        last_message=str(canonical_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(archived_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)

    result = runner.invoke(cli.app, ["cleanup", "--dry-run"])

    saved = RunRecord.load(archived_dir / "run.json")
    assert result.exit_code == 0
    assert "Would repair" in result.stdout
    assert "archived run metadata" in result.stdout
    assert saved.prompt == str(canonical_dir / "prompt.md")


def test_cleanup_repairs_archived_run_metadata(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    canonical_dir = tmp_path / ".flow" / "runs" / "bd-1"
    archived_dir = tmp_path / ".flow" / "runs" / "bd-1-attempt-1"
    actual_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"
    actual_result = actual_worktree / ".c3x" / "result.json"
    actual_result.parent.mkdir(parents=True)
    actual_result.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="Done").model_dump_json(),
        encoding="utf-8",
    )
    archived_dir.mkdir(parents=True)
    (archived_dir / "prompt.md").write_text("old prompt", encoding="utf-8")
    (archived_dir / "last-message.md").write_text(
        f"Result written to [`.c3x/result.json`]({actual_result}).\n",
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(canonical_dir / "prompt.md"),
        result=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix" / ".c3x" / "result.json"),
        last_message=str(canonical_dir / "last-message.md"),
        status="blocked",
        attempt=1,
    ).save(archived_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)

    result = runner.invoke(cli.app, ["cleanup"])

    saved = RunRecord.load(archived_dir / "run.json")
    assert result.exit_code == 0
    assert "Repaired" in result.stdout
    assert saved.prompt == str(archived_dir / "prompt.md")
    assert saved.last_message == str(archived_dir / "last-message.md")
    assert saved.result == str(actual_result)
    assert saved.worktree == str(actual_worktree)


def test_cleanup_repairs_run_attempt_and_branch_from_actual_worktree(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    actual_worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"
    actual_result = actual_worktree / ".c3x" / "result.json"
    actual_result.parent.mkdir(parents=True)
    actual_result.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="Done").model_dump_json(),
        encoding="utf-8",
    )
    last_message = run_dir / "last-message.md"
    last_message.parent.mkdir(parents=True)
    last_message.write_text(f"Result written to [`.c3x/result.json`]({actual_result}).\n", encoding="utf-8")
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-2",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-2"),
        prompt=str(run_dir / "prompt.md"),
        result=str(
            tmp_path
            / ".flow"
            / "worktrees"
            / "c3x-bd-1-fix-attempt-2"
            / ".c3x"
            / "result.json"
        ),
        last_message=str(last_message),
        status="reviewed",
        attempt=2,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli,
        "worktree_branches",
        lambda root: {actual_worktree: "c3x/bd-1-fix-attempt-3"},
    )
    branch_lookups: list[Path] = []

    def fake_current_branch(worktree: Path) -> str:
        branch_lookups.append(worktree)
        return "c3x/bd-1-fix-attempt-3"

    monkeypatch.setattr(cli, "current_branch", fake_current_branch)

    result = runner.invoke(cli.app, ["cleanup", "--dry-run"])

    assert result.exit_code == 0
    assert "current run metadata" in result.stdout
    assert branch_lookups == []

    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    saved = RunRecord.load(run_dir / "run.json")
    assert saved.attempt == 3
    assert saved.branch == "c3x/bd-1-fix-attempt-3"
    assert saved.worktree == str(actual_worktree)
    assert branch_lookups == [actual_worktree]


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
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
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
    worktree.mkdir(parents=True)
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
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: False)
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


def test_cleanup_ignores_landed_record_when_branch_and_worktree_are_missing(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
        attempt=1,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: False)
    monkeypatch.setattr(
        cli,
        "is_ancestor",
        lambda root, ancestor, descendant: (_ for _ in ()).throw(
            cli.GitError("fatal: Not a valid object name c3x/bd-1-fix")
        ),
    )

    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    assert "Nothing to clean" in result.stdout


def test_cleanup_repair_beads_compacts_oversized_flow_payload(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="bloated",
        status="blocked",
        description="large description\n" * 900,
        notes="large notes\n" * 900,
        labels=("flow", "blocked"),
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["cleanup", "--repair-beads"])

    assert result.exit_code == 0
    assert result.stdout.count("Repaired Beads payload") == 1
    assert "KiB ->" in result.stdout
    assert "Nothing to clean" in result.stdout
    assert beads.compacted[0][0] == "bd-1"
    assert "too large for Beads event-log updates" in beads.compacted[0][1]
    assert "bd restore bd-1" in beads.compacted[0][1]


def test_cleanup_repair_beads_dry_run_leaves_payload(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    description = "large description\n" * 900
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="bloated",
        description=description,
        labels=("flow", "blocked"),
    )
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["cleanup", "--repair-beads", "--dry-run"])

    assert result.exit_code == 0
    assert "Would repair Beads payload" in result.stdout
    assert "KiB ->" in result.stdout
    assert beads.compacted == []
    assert beads.items["bd-1"].description == description


def test_cleanup_repair_beads_allows_non_landed_target(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="bloated",
        description="large description\n" * 900,
        labels=("flow", "blocked"),
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
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["cleanup", "bd-1", "--repair-beads"])

    assert result.exit_code == 0
    assert "Repaired Beads payload" in result.stdout
    assert "KiB ->" in result.stdout
    assert "Nothing to clean" in result.stdout
    assert beads.compacted[0][0] == "bd-1"


def test_cleanup_reconciles_running_retry_labels(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="retry",
        status="in_progress",
        labels=("flow", "running", "land-blocked", "blocker-merge-conflict", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-attempt-3",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3"),
        prompt=str(run_dir / "prompt.md"),
        result=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-attempt-3" / ".c3x" / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="running",
        attempt=3,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["cleanup", "bd-1"])

    assert result.exit_code == 0
    assert "Reconciled labels" in result.stdout
    assert ("bd-1", ["blocker-merge-conflict", "land-blocked", "reviewed"]) in beads.removed_labels


def test_cleanup_recommends_fresh_retry_for_conflicting_labels(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="confused",
        status="in_progress",
        labels=("flow", "reviewed", "land-blocked", "blocker-merge-conflict"),
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
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["cleanup", "bd-1"])

    assert result.exit_code == 0
    assert "Label conflict" in result.stdout
    assert "c3x retry bd-1 --fresh" in result.stdout


def test_cleanup_reconciles_landed_labels(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="landed",
        status="closed",
        labels=("flow", "landed", "land-blocked", "blocker-merge-conflict", "reviewed"),
    )
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
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: True)
    monkeypatch.setattr(cli, "remove_worktree", lambda root, path, force=False: None)
    monkeypatch.setattr(cli, "delete_branch", lambda root, branch, force=False: None)

    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    assert "Reconciled labels" in result.stdout
    assert ("bd-1", ["blocker-merge-conflict", "land-blocked", "reviewed"]) in beads.removed_labels


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
    monkeypatch.setattr(
        cli,
        "run_verification",
        lambda root, commands: (_ for _ in ()).throw(AssertionError("dry-run should not run verification")),
    )

    result = runner.invoke(cli.app, ["unstick"])

    assert result.exit_code == 0
    assert "bd-1" in result.stdout
    assert "Dry run only" in result.stdout
    assert "bd-1" in beads.items


def test_unstick_scans_run_records_once_and_defers_cleanup_lookup(monkeypatch, tmp_path: Path) -> None:
    class FastUnstickBeads(_RecordingBeads):
        def dependencies(self, task_id: str, *, direction: str = "down", dep_type: str = "blocks") -> list[dict[str, str]]:
            raise AssertionError("cleanup dependencies should only be checked for repair candidates")

    beads = FastUnstickBeads()
    beads.items["bd-1"] = BeadSummary(id="bd-1", title="queued", labels=("flow", "ready"))
    beads.items["bd-2"] = BeadSummary(id="bd-2", title="inbox", labels=("flow", "inbox"))
    calls = 0

    def fake_run_record_paths(root: Path) -> list[tuple[Path, RunRecord]]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(cli, "_run_record_paths", fake_run_record_paths)

    candidates = cli._unstick_candidates(tmp_path, beads, task_id=None, verify_mode="none")

    assert candidates == []
    assert calls == 1


def test_unstick_fix_removes_stale_running_worker_state(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        pid=12345,
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "_process_is_running", lambda pid: False)

    result = runner.invoke(cli.app, ["unstick", "--fix", "--verify", "none"])

    assert result.exit_code == 0
    assert "mark-blocked-stale-running" in result.stdout
    saved = RunRecord.load(run_dir / "run.json")
    assert saved.status == "blocked"
    assert saved.outcome == "worker-not-live"
    assert saved.pid is None
    assert ("bd-1", ["flow", "blocked", "blocker-worker-not-live"]) in beads.added_labels
    assert ("bd-1", ["running", "reviewing"]) in beads.removed_labels


def test_unstick_does_not_close_contained_dirty_worktree(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
        finished_at="2026-05-25T00:00:00+00:00",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "is_ancestor", lambda root, branch, descendant: True)
    monkeypatch.setattr(cli, "worktree_has_changes", lambda path: True)

    candidates = cli._unstick_candidates(tmp_path, beads, task_id="bd-1", verify_mode="none")

    assert candidates == []


def test_unstick_detects_completed_result_for_blocked_task(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "blocked", "blocker-result-missing"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    result_path = worktree / ".c3x" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(result_path),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        outcome="missing-result",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["unstick", "bd-1", "--verify", "none"])
    candidates = cli._unstick_candidates(tmp_path, beads, task_id="bd-1", verify_mode="none")

    assert result.exit_code == 0
    assert [candidate.action for candidate in candidates] == ["mark-completed-from-result"]


def test_unstick_fix_marks_completed_result_reviewing(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "blocked", "blocker-result-missing", "landed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    result_path = worktree / ".c3x" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(result_path),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        outcome="missing-result",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["unstick", "bd-1", "--fix", "--verify", "none"])

    saved = RunRecord.load(run_dir / "run.json")
    assert result.exit_code == 0
    assert "Repaired" in result.stdout
    assert saved.status == "completed"
    assert saved.outcome == "completed"
    assert saved.result == str(run_dir / "result.json")
    assert (run_dir / "result.json").exists()
    assert ("bd-1", "in_progress") in beads.statuses
    assert ("bd-1", ["flow", "reviewing", "completed-by-agent"]) in beads.added_labels
    removed = [labels[0] for item_id, labels in beads.removed_labels if item_id == "bd-1"]
    assert "blocker-result-missing" in removed
    assert "landed" in removed


def test_unstick_does_not_clear_review_cleanup_blockers(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "blocked", "review-blocked", "blocker-review-issues"),
    )
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Fix review issue for bd-1: add test",
        description="Blocks: bd-1\n\nAdd missing test.",
        labels=("flow", "ready", "review-fix"),
    )
    beads.blockers.append(("bd-2", "bd-1"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    result_path = worktree / ".c3x" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(result_path),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        outcome="review-blocked",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    result = runner.invoke(cli.app, ["unstick", "bd-1", "--fix", "--verify", "none"])
    candidates = cli._unstick_candidates(tmp_path, beads, task_id="bd-1", verify_mode="none")

    assert result.exit_code == 0
    assert "Skipped" in result.stdout
    assert candidates[0].verification_issues == ("open review cleanup blockers must be fixed first: bd-2",)
    assert beads.blockers == [("bd-2", "bd-1")]
    assert "bd-2" in beads.items
    assert RunRecord.load(run_dir / "run.json").status == "blocked"


def test_unstick_closes_review_blocked_task_when_cleanup_tasks_are_closed(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "blocked", "review-blocked", "blocker-review-issues"),
    )
    beads.items["bd-2"] = BeadSummary(
        id="bd-2",
        title="Fix review issue for bd-1: add test",
        description="Blocks: bd-1",
        status="closed",
        labels=("flow", "landed", "review-fix"),
    )
    beads.blockers.append(("bd-2", "bd-1"))
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="blocked",
        outcome="review-blocked",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: "resolved123")

    result = runner.invoke(cli.app, ["unstick", "bd-1", "--fix", "--verify", "none"])

    saved = RunRecord.load(run_dir / "run.json")
    assert result.exit_code == 0
    assert "close-review-resolved" in result.stdout
    assert saved.status == "landed"
    assert saved.outcome == "review-resolved"
    assert saved.landing_branch == "feature"
    assert saved.landed_revision == "resolved123"
    assert ("bd-1", "Resolved by closed review cleanup blockers") in beads.closed


def test_unstick_fix_cascades_closed_review_cleanup_chains(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()

    class _CascadeBeads(_RecordingBeads):
        def list_active(self) -> list[BeadSummary]:
            return [item for item in self.items.values() if item.status != "closed"]

        def close(self, task_id: str, note: str) -> None:
            self.closed.append((task_id, note))
            self.items[task_id] = replace(self.items[task_id], status="closed")

    beads = _CascadeBeads()
    for task_id, blocked_id, status in (
        ("bd-parent", "", "in_progress"),
        ("bd-child", "bd-parent", "in_progress"),
        ("bd-leaf", "bd-child", "closed"),
    ):
        beads.items[task_id] = BeadSummary(
            id=task_id,
            title=task_id,
            description=f"Blocks: {blocked_id}" if blocked_id else "",
            status=status,
            labels=(
                ("flow", "landed", "review-fix")
                if status == "closed"
                else ("flow", "blocked", "review-blocked", "blocker-review-issues", "review-fix")
            ),
        )
        if blocked_id:
            beads.blockers.append((task_id, blocked_id))
        if status != "closed":
            run_dir = tmp_path / ".flow" / "runs" / task_id
            RunRecord(
                task_id=task_id,
                branch=f"c3x/{task_id}",
                worktree=str(tmp_path / ".flow" / "worktrees" / task_id),
                prompt=str(run_dir / "prompt.md"),
                result=str(run_dir / "result.json"),
                last_message=str(run_dir / "last-message.md"),
                status="blocked",
                outcome="review-blocked",
            ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: "resolved123")

    result = runner.invoke(cli.app, ["unstick", "--fix", "--verify", "none"])

    assert result.exit_code == 0
    assert [task_id for task_id, _ in beads.closed] == ["bd-child", "bd-parent"]
    assert RunRecord.load(tmp_path / ".flow" / "runs" / "bd-child" / "run.json").outcome == "review-resolved"
    assert RunRecord.load(tmp_path / ".flow" / "runs" / "bd-parent" / "run.json").outcome == "review-resolved"


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
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)
    monkeypatch.setattr(cli, "history_has_subject", lambda root, revision, subject: False)
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
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)
    monkeypatch.setattr(cli, "history_has_subject", lambda root, revision, subject: False)
    monkeypatch.setattr(cli, "branch_diff_summary", lambda root, branch: "Diff stat:\n file.ts | 2 +")
    monkeypatch.setattr(cli, "merge_branch", lambda root, branch: calls.append(("merge", branch)))

    result = runner.invoke(cli.app, ["cleanup", "bd-1"], input="n\n")

    assert result.exit_code == 0
    assert "Skipped" in result.stdout
    assert calls == []


def test_cleanup_does_not_merge_review_resolved_branch(monkeypatch, tmp_path: Path) -> None:
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
        outcome="review-resolved",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)

    actions = cli._cleanup_actions(tmp_path, task_id="bd-1")

    assert len(actions) == 1
    assert actions[0].reason == "landed worktree"
    assert not actions[0].repair_merge


def test_cleanup_uses_historical_task_commit_when_branch_tip_advanced(monkeypatch, tmp_path: Path) -> None:
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
        outcome="landed",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(cli, "is_ancestor", lambda root, ancestor, descendant: False)
    monkeypatch.setattr(
        cli,
        "history_has_subject",
        lambda root, revision, subject: subject == "Complete c3x task bd-1",
    )

    actions = cli._cleanup_actions(tmp_path, task_id="bd-1")

    assert len(actions) == 1
    assert actions[0].reason == "landed worktree"
    assert not actions[0].repair_merge


def test_cleanup_uses_recorded_landing_revision_when_source_branch_tip_advanced(monkeypatch, tmp_path: Path) -> None:
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
        outcome="landed",
        landing_branch="feature",
        landed_revision="landed123",
    ).save(run_dir / "run.json")
    monkeypatch.setattr(cli, "worktree_branches", lambda root: {})
    monkeypatch.setattr(cli, "local_branch_exists", lambda root, branch: True)
    monkeypatch.setattr(
        cli,
        "is_ancestor",
        lambda root, ancestor, descendant: ancestor == "landed123" and descendant == "feature",
    )

    actions = cli._cleanup_actions(tmp_path, task_id="bd-1")

    assert len(actions) == 1
    assert actions[0].reason == "landed worktree"
    assert not actions[0].repair_merge


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
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")
    monkeypatch.setattr(cli, "rev_parse", lambda root, revision: "landed123")
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
    monkeypatch.setattr(cli, "current_branch", lambda root: "feature")

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
        labels=("flow", "land-blocked", "blocker-merge-conflict"),
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
        attempt: int | None = None,
    ) -> RunRecord:
        captured.update(
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "target_revision": target_revision,
                "original_result": original_result,
                "attempt": attempt,
            }
        )
        record = RunRecord(
            task_id=task.id,
            branch="c3x/bd-1-fix-conflict-attempt-2",
            worktree=str(tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-conflict-attempt-2"),
            prompt=str(tmp_path / ".flow" / "runs" / "bd-1" / "prompt.md"),
            result=str(
                tmp_path
                / ".flow"
                / "worktrees"
                / "c3x-bd-1-fix-conflict-attempt-2"
                / ".c3x"
                / "result.json"
            ),
            last_message=str(tmp_path / ".flow" / "runs" / "bd-1" / "last-message.md"),
            attempt=attempt or 2,
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
    assert captured["attempt"] == 2
    assert (tmp_path / ".flow" / "runs" / "bd-1-attempt-2" / "run.json").exists()
    seeded = tmp_path / ".flow" / "runs" / "bd-1" / "result.json"
    assert seeded.exists()
    assert WorkerResult.model_validate_json(seeded.read_text(encoding="utf-8")).status == "completed"
    assert ("bd-1", "in_progress") in beads.statuses
    assert any("conflict-resolver" in labels for task_id, labels in beads.added_labels if task_id == "bd-1")


def test_conflict_task_ids_skip_running_stale_land_blockers(tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-running"] = BeadSummary(
        id="bd-running",
        title="running retry",
        labels=("flow", "running", "land-blocked", "blocker-merge-conflict"),
    )
    beads.items["bd-blocked"] = BeadSummary(
        id="bd-blocked",
        title="blocked conflict",
        labels=("flow", "land-blocked", "blocker-merge-conflict"),
    )

    assert cli._conflict_task_ids(tmp_path, beads, task_id=None, all_tasks=True) == ["bd-blocked"]


def test_conflict_task_ids_skip_already_landed_records(tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-landed"] = BeadSummary(
        id="bd-landed",
        title="stale conflict labels",
        labels=("flow", "land-blocked", "blocker-merge-conflict"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-landed"
    RunRecord(
        task_id="bd-landed",
        branch="c3x/bd-landed",
        worktree=str(tmp_path / ".flow" / "worktrees" / "bd-landed"),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="landed",
    ).save(run_dir / "run.json")

    assert cli._conflict_task_ids(tmp_path, beads, task_id=None, all_tasks=True) == []


def test_land_stops_when_root_is_dirty(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_warn_if_risky_flow_branch", lambda root: None)
    monkeypatch.setattr(cli, "worktree_has_changes", lambda root, ignored_prefixes=None: True)

    result = runner.invoke(cli.app, ["land", "bd-1"])
    assert result.exit_code != 0
    assert "root worktree has uncommitted changes" in result.stdout


def test_unstick_stops_when_root_is_dirty(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "worktree_has_changes", lambda root, ignored_prefixes=None: True)

    result = runner.invoke(cli.app, ["unstick", "--fix"])
    assert result.exit_code != 0
    assert "root worktree has uncommitted changes" in result.stdout


def test_apply_unstick_commits_changes_when_worker_is_dirty(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        labels=("flow", "running", "reviewed"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"
    worktree.mkdir(parents=True)
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(run_dir / "result.json"),
        last_message=str(run_dir / "last-message.md"),
        status="completed",
    ).save(run_dir / "run.json")

    committed_worktrees = []
    monkeypatch.setattr(cli, "worktree_has_changes", lambda path, ignored_prefixes=None: True)
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: committed_worktrees.append(path))

    candidate = cli.UnstickCandidate(
        task_id="bd-1",
        action="mark-reviewed",
        reason="test",
        record_status="completed",
        bead_status="in_progress",
    )

    cli._apply_unstick_candidate(tmp_path, beads, candidate)
    assert committed_worktrees == [worktree]


def test_import_finished_results_commits_conflict_resolver_changes(monkeypatch, tmp_path: Path) -> None:
    beads = _RecordingBeads()
    beads.items["bd-1"] = BeadSummary(
        id="bd-1",
        title="fix",
        status="in_progress",
        labels=("flow", "running"),
    )
    run_dir = tmp_path / ".flow" / "runs" / "bd-1"
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix-conflict"
    result_path = worktree / ".c3x" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        WorkerResult(task_id="bd-1", status="completed", summary="done").model_dump_json(),
        encoding="utf-8",
    )
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1-fix-conflict",
        worktree=str(worktree),
        prompt=str(run_dir / "prompt.md"),
        result=str(result_path),
        last_message=str(run_dir / "last-message.md"),
        status="running",
        task_type="conflict_resolver",
    ).save(run_dir / "run.json")

    committed_worktrees = []
    monkeypatch.setattr(cli, "commit_worktree_changes", lambda path, message: committed_worktrees.append(path))
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_beads", lambda root: beads)

    cli._import_finished_results(tmp_path, beads)
    assert committed_worktrees == [worktree]
