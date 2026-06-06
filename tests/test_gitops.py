import subprocess
from pathlib import Path

import pytest

from c3x import gitops


def test_worktree_branches_parses_porcelain_output(monkeypatch, tmp_path: Path) -> None:
    worktree = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"

    def fake_git(root, args, *, capture=False, allow_exit_codes=None):
        assert root == tmp_path
        assert args == ["worktree", "list", "--porcelain"]
        return subprocess.CompletedProcess(
            ["git", *args],
            0,
            stdout=(
                f"worktree {tmp_path}\n"
                "HEAD abc123\n"
                "branch refs/heads/main\n\n"
                f"worktree {worktree}\n"
                "HEAD def456\n"
                "branch refs/heads/c3x/bd-1-fix\n"
            ),
        )

    monkeypatch.setattr(gitops, "_git", fake_git)

    assert gitops.worktree_branches(tmp_path) == {
        tmp_path: "main",
        worktree: "c3x/bd-1-fix",
    }


def test_delete_branch_ignores_already_missing_branch(monkeypatch, tmp_path: Path) -> None:
    def fake_git(root: Path, args: list[str]) -> None:
        raise gitops.GitError("error: branch 'c3x/bd-1-fix' not found.")

    monkeypatch.setattr(gitops, "_git", fake_git)

    gitops.delete_branch(tmp_path, "c3x/bd-1-fix")


def test_delete_branch_reraises_other_branch_failures(monkeypatch, tmp_path: Path) -> None:
    def fake_git(root: Path, args: list[str]) -> None:
        raise gitops.GitError("error: The branch is not fully merged.")

    monkeypatch.setattr(gitops, "_git", fake_git)

    with pytest.raises(gitops.GitError, match="not fully merged"):
        gitops.delete_branch(tmp_path, "c3x/bd-1-fix")


def test_commit_worktree_changes_reports_missing_worktree(tmp_path: Path) -> None:
    missing = tmp_path / ".flow" / "worktrees" / "c3x-bd-1-fix"

    with pytest.raises(gitops.GitError, match="worktree is missing"):
        gitops.commit_worktree_changes(missing, "Complete c3x task bd-1")
