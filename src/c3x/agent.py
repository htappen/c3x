from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from c3x.beads import BeadSummary
from c3x.config import C3xConfig
from c3x.gitops import create_worktree, task_branch
from c3x.paths import last_message_path, prompt_path, run_record_path, runs_dir, worktrees_dir
from c3x.prompt_policy import caveman_mode_text
from c3x.schema import RunRecord


class AgentError(RuntimeError):
    pass


def start_worker(root: Path, config: C3xConfig, task: BeadSummary) -> RunRecord:
    attempt = _next_attempt(root, task.id)
    branch = task_branch(task.id, task.title)
    worktree = worktrees_dir(root) / branch.replace("/", "-")
    create_worktree(root, branch, worktree)

    run_path = run_record_path(root, task.id)
    prompt = prompt_path(root, task.id)
    result = worktree / ".c3x" / "result.json"
    last_message = last_message_path(root, task.id)
    prompt.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(_worker_prompt(task, result), encoding="utf-8")

    command = _agent_command(config, worktree, prompt, result, last_message)
    process = subprocess.Popen(
        command,
        cwd=worktree,
        text=True,
        stdout=(prompt.parent / "stdout.log").open("w", encoding="utf-8"),
        stderr=(prompt.parent / "stderr.log").open("w", encoding="utf-8"),
    )
    record = RunRecord(
        task_id=task.id,
        branch=branch,
        worktree=str(worktree),
        prompt=str(prompt),
        result=str(result),
        last_message=str(last_message),
        pid=process.pid,
        attempt=attempt,
    )
    record.save(run_path)
    return record


def _agent_command(
    config: C3xConfig,
    worktree: Path,
    prompt: Path,
    result: Path,
    last_message: Path,
) -> list[str]:
    executable = shlex.split(config.agents.codex_command)
    if not executable:
        raise AgentError("agents.codex_command cannot be empty")
    mapping = {
        "model": config.models.worker,
        "worktree": str(worktree),
        "prompt": str(prompt),
        "result": str(result),
        "last_message": str(last_message),
    }
    args = [arg.format(**mapping) for arg in config.agents.codex_args]
    return [*executable, *args]


def _next_attempt(root: Path, task_id: str) -> int:
    attempts = 0
    for path in sorted(runs_dir(root).glob("*/run.json")):
        try:
            record = RunRecord.load(path)
        except Exception:
            continue
        if record.task_id == task_id:
            attempts = max(attempts, record.attempt)
    return attempts + 1


def _worker_prompt(task: BeadSummary, result: Path) -> str:
    return f"""{caveman_mode_text()}

# c3x worker task

Task: {task.id}
Title: {task.title}

Work on exactly this task in the current git worktree.

Write a structured JSON result to:
{result}

Required result shape:
```json
{{
  "task_id": "{task.id}",
  "status": "completed",
  "summary": "What changed",
  "task_kind": "feature|bug|test|refactor|docs|infra|spike",
  "attempt": 1,
  "changed_files": [],
  "verification": [],
  "blockers": [],
  "blocker_category": null,
  "proposed_tasks": [],
  "scope_expansion": [],
  "confidence": "high",
  "unfinished": []
}}
```

If stuck, set `status` to `blocked` or `failed`, fill `blocker_category`, `blockers`, and `unfinished`, and do not pretend the task is solved.
"""
