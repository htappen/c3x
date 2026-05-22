from pathlib import Path

from typer.testing import CliRunner

from c3x import cli
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
