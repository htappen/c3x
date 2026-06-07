from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitMergeConflict(GitError):
    def __init__(self, branch: str, files: list[str], detail: str) -> None:
        self.branch = branch
        self.files = files
        self.detail = detail
        message = f"merge conflict while merging `{branch}`"
        if files:
            message += ": " + ", ".join(files)
        if detail:
            message += f"\n{detail}"
        super().__init__(message)


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


def worktree_branches(root: Path) -> dict[Path, str]:
    result = _git(root, ["worktree", "list", "--porcelain"], capture=True)
    branches: dict[Path, str] = {}
    worktree: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            worktree = Path(line.removeprefix("worktree "))
        elif worktree is not None and line.startswith("branch refs/heads/"):
            branches[worktree] = line.removeprefix("branch refs/heads/")
    return branches


def local_branch_exists(root: Path, branch: str) -> bool:
    result = _git(root, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], allow_exit_codes={0, 1})
    return result.returncode == 0


def merge_branch(root: Path, branch: str) -> None:
    commit_ledger_changes(root, "Checkpoint c3x ledger before merge")
    result = _git(
        root,
        ["merge", "--no-ff", branch, "-m", f"Merge {branch}"],
        capture=True,
        allow_exit_codes={0, 1},
    )
    if result.returncode != 0:
        files = conflicted_files(root)
        detail = result.stderr.strip() or result.stdout.strip()
        if files:
            _git(root, ["merge", "--abort"], allow_exit_codes={0, 1})
            raise GitMergeConflict(branch, files, detail)
        raise GitError(f"`git merge --no-ff {branch}` failed: {detail}")
    if not is_ancestor(root, branch, "HEAD"):
        raise GitError(f"`{branch}` was not merged into HEAD")


def create_conflict_resolution_worktree(
    root: Path,
    *,
    branch: str,
    source_branch: str,
    worktree: Path,
) -> list[str]:
    create_worktree(root, branch, worktree)
    result = _git(
        worktree,
        ["merge", "--no-ff", "--no-commit", source_branch],
        capture=True,
        allow_exit_codes={0, 1},
    )
    if result.returncode == 0:
        return []
    files = conflicted_files(worktree)
    if not files:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitError(f"`git merge --no-ff --no-commit {source_branch}` failed: {detail}")
    return files


def commit_worktree_changes(worktree: Path, message: str) -> None:
    if not worktree.exists():
        raise GitError(f"worktree is missing: {worktree}")
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


def worktree_has_changes(worktree: Path, *, ignored_prefixes: tuple[str, ...] = (".c3x/",)) -> bool:
    if not (worktree / ".git").exists():
        return False
    try:
        result = _git(worktree, ["status", "--porcelain", "--untracked-files=all"], capture=True)
    except GitError:
        return False
    for line in result.stdout.splitlines():
        path = line[3:]
        if not path:
            continue
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in ignored_prefixes):
            continue
        return True
    return False


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


def conflicted_files(root: Path) -> list[str]:
    result = _git(root, ["diff", "--name-only", "--diff-filter=U"], capture=True, allow_exit_codes={0, 1})
    return [line for line in result.stdout.splitlines() if line.strip()]


def commit_subject(root: Path, rev: str) -> str:
    result = _git(root, ["log", "-1", "--pretty=%s", rev], capture=True)
    return result.stdout.strip()


def history_has_subject(root: Path, rev: str, subject: str) -> bool:
    result = _git(root, ["log", rev, "--format=%s", "--fixed-strings", f"--grep={subject}"], capture=True)
    return subject in result.stdout.splitlines()


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
    try:
        _git(root, ["branch", "-D" if force else "-d", branch])
    except GitError as exc:
        if "not found" in str(exc).lower():
            return
        raise


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
