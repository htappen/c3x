from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:48] or "task"


def task_branch(task_id: str, title: str) -> str:
    return f"c3x/{task_id}-{slug(title)}"


def create_worktree(root: Path, branch: str, worktree: Path) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if worktree.exists():
        return
    _git(root, ["worktree", "add", "-b", branch, str(worktree), "HEAD"])


def changed_files(root: Path, base: str = "HEAD") -> list[str]:
    result = _git(root, ["diff", "--name-only", base], capture=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def current_branch(root: Path) -> str:
    result = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    return result.stdout.strip()


def merge_branch(root: Path, branch: str) -> None:
    commit_ledger_changes(root, "Checkpoint c3x ledger before merge")
    _git(root, ["merge", "--no-ff", branch, "-m", f"Merge {branch}"])


def remove_worktree(root: Path, worktree: Path, *, force: bool = False) -> None:
    if worktree.exists():
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        _git(root, [*args, str(worktree)])


def delete_branch(root: Path, branch: str, *, force: bool = False) -> None:
    _git(root, ["branch", "-D" if force else "-d", branch])


def commit_ledger_changes(root: Path, message: str) -> None:
    paths = _ledger_paths(root)
    if not paths:
        return
    _git(root, ["add", *paths])
    result = _git(root, ["diff", "--cached", "--quiet"], allow_exit_codes={0, 1})
    if result.returncode == 0:
        return
    _git(root, ["commit", "-m", message])


def _ledger_paths(root: Path) -> list[str]:
    result = _git(root, ["status", "--porcelain"], capture=True)
    paths: list[str] = []
    allowed = (".beads/", ".claude/")
    exact = {".gitignore", "AGENTS.md", "CLAUDE.md"}
    for line in result.stdout.splitlines():
        path = line[3:]
        if path.startswith(allowed) or path in exact:
            paths.append(path)
    return paths


def _git(
    root: Path,
    args: list[str],
    *,
    capture: bool = False,
    allow_exit_codes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        check=False,
    )
    allowed = allow_exit_codes or {0}
    if result.returncode not in allowed:
        detail = result.stderr.strip() or (result.stdout or "").strip()
        raise GitError(f"`git {' '.join(args)}` failed: {detail}")
    return result
