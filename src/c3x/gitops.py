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
    if not is_ancestor(root, branch, "HEAD"):
        raise GitError(f"`{branch}` was not merged into HEAD")


def commit_worktree_changes(worktree: Path, message: str) -> None:
    paths = _changed_worktree_paths(worktree)
    if not paths:
        return
    _git(worktree, ["add", *paths])
    result = _git(worktree, ["diff", "--cached", "--quiet"], allow_exit_codes={0, 1})
    if result.returncode == 0:
        return
    _git(worktree, ["commit", "-m", message])


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = _git(root, ["merge-base", "--is-ancestor", ancestor, descendant], allow_exit_codes={0, 1})
    return result.returncode == 0


def branch_diff_summary(root: Path, branch: str) -> str:
    commits = _git(root, ["log", "--oneline", f"HEAD..{branch}"], capture=True)
    stat = _git(root, ["diff", "--stat", f"HEAD..{branch}"], capture=True)
    status = _git(root, ["status", "--short"], capture=True)
    parts = []
    if commits.stdout.strip():
        parts.append("Commits:\n" + commits.stdout.strip())
    if stat.stdout.strip():
        parts.append("Diff stat:\n" + stat.stdout.strip())
    if status.stdout.strip():
        parts.append("Root status:\n" + status.stdout.strip())
    return "\n\n".join(parts) or "No diff or status output."


def commit_subject(root: Path, rev: str) -> str:
    result = _git(root, ["log", "-1", "--pretty=%s", rev], capture=True)
    return result.stdout.strip()


def commit_parents(root: Path, rev: str) -> list[str]:
    result = _git(root, ["rev-list", "--parents", "-n", "1", rev], capture=True)
    parts = result.stdout.strip().split()
    return parts[1:]


def rev_parse(root: Path, rev: str) -> str:
    result = _git(root, ["rev-parse", rev], capture=True)
    return result.stdout.strip()


def ensure_rewrite_safe(root: Path) -> None:
    result = _git(root, ["status", "--porcelain"], capture=True)
    dirty = [
        line
        for line in result.stdout.splitlines()
        if line[3:] and not line[3:].startswith(".flow/")
    ]
    if dirty:
        raise GitError("git worktree has uncommitted changes:\n" + "\n".join(dirty))


def squash_head_to(root: Path, base: str, message: str) -> None:
    _git(root, ["reset", "--soft", base])
    _git(root, ["commit", "-m", message])


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


def _changed_worktree_paths(worktree: Path) -> list[str]:
    result = _git(worktree, ["status", "--porcelain"], capture=True)
    paths: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:]
        if not path or path.startswith(".c3x/"):
            continue
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
