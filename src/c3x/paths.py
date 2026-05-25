from __future__ import annotations

from pathlib import Path

from c3x.config import FLOW_DIR


def flow_dir(root: Path) -> Path:
    return root / FLOW_DIR


def runs_dir(root: Path) -> Path:
    return flow_dir(root) / "runs"


def agents_dir(root: Path) -> Path:
    return flow_dir(root) / "agents"


def worktrees_dir(root: Path) -> Path:
    return flow_dir(root) / "worktrees"


def run_dir(root: Path, task_id: str) -> Path:
    return runs_dir(root) / task_id


def run_record_path(root: Path, task_id: str) -> Path:
    return run_dir(root, task_id) / "run.json"


def result_path(root: Path, task_id: str) -> Path:
    return run_dir(root, task_id) / "result.json"


def prompt_path(root: Path, task_id: str) -> Path:
    return run_dir(root, task_id) / "prompt.md"


def last_message_path(root: Path, task_id: str) -> Path:
    return run_dir(root, task_id) / "last-message.md"


def pause_path(root: Path) -> Path:
    return flow_dir(root) / "paused"


def activity_path(root: Path) -> Path:
    return flow_dir(root) / "activity.json"
