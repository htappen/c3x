from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from c3x import cli
from c3x.beads import BeadSummary
from c3x.schema import RunRecord


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
