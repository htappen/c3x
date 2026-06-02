from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from c3x.beads import BeadSummary
from c3x.config import C3xConfig
from c3x.gitops import create_conflict_resolution_worktree, create_worktree, task_branch
from c3x.paths import run_log_dir, run_record_path, runs_dir, worktrees_dir
from c3x.prompt_policy import caveman_mode_text
from c3x.schema import ReviewResult, RunRecord, WorkerResult


class AgentError(RuntimeError):
    pass


def _worker_result_path(worktree: Path, task_id: str) -> Path:
    return worktree / ".c3x" / f"{_safe_result_prefix(task_id)}-result.json"


def _safe_result_prefix(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id).strip("-") or "task"


def start_worker(root: Path, config: C3xConfig, task: BeadSummary, *, attempt: int | None = None) -> RunRecord:
    attempt = attempt or _next_attempt(root, task.id)
    branch = _attempt_branch(task.id, task.title, attempt)
    worktree = worktrees_dir(root) / branch.replace("/", "-")
    create_worktree(root, branch, worktree)

    task_type = "worker"
    run_path = run_record_path(root, task.id)
    log_dir = run_log_dir(root, task.id, task_type, attempt)
    prompt = log_dir / "prompt.md"
    result = _worker_result_path(worktree, task.id)
    last_message = log_dir / "last-message.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(_worker_prompt(task, result), encoding="utf-8")

    provider = _provider_for_task(config, task_type)
    command = _agent_command(config, worktree, prompt, result, last_message, task_type=task_type)
    process = subprocess.Popen(
        command,
        cwd=worktree,
        text=True,
        stdout=(prompt.parent / "stdout.log").open("w", encoding="utf-8"),
        stderr=(prompt.parent / "stderr.log").open("w", encoding="utf-8"),
        start_new_session=True,
    )
    record = RunRecord(
        task_id=task.id,
        branch=branch,
        worktree=str(worktree),
        prompt=str(prompt),
        result=str(result),
        last_message=str(last_message),
        provider=provider,
        task_type=task_type,
        pid=process.pid,
        attempt=attempt,
    )
    record.save(run_path)
    return record


def resume_session_worker(
    root: Path,
    config: C3xConfig,
    task: BeadSummary,
    previous: RunRecord,
    *,
    session_id: str,
    reason: str = "",
    attempt: int | None = None,
) -> RunRecord:
    attempt = attempt or _next_attempt(root, task.id)
    worktree = Path(previous.worktree)
    if not worktree.exists():
        raise AgentError(f"cannot continue {task.id}: previous worktree is missing: {worktree}")

    task_type = "worker"
    run_path = run_record_path(root, task.id)
    log_dir = run_log_dir(root, task.id, task_type, attempt)
    prompt = log_dir / "prompt.md"
    result = _worker_result_path(worktree, task.id)
    last_message = log_dir / "last-message.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        _resume_worker_prompt(task, result, previous=previous, reason=reason),
        encoding="utf-8",
    )

    provider = _provider_for_task(config, task_type)
    command = _agent_command(
        config,
        worktree,
        prompt,
        result,
        last_message,
        task_type=task_type,
        resume_session_id=session_id,
    )
    process = subprocess.Popen(
        command,
        cwd=worktree,
        text=True,
        stdout=(prompt.parent / "stdout.log").open("w", encoding="utf-8"),
        stderr=(prompt.parent / "stderr.log").open("w", encoding="utf-8"),
        start_new_session=True,
    )
    record = RunRecord(
        task_id=task.id,
        branch=previous.branch,
        worktree=str(worktree),
        prompt=str(prompt),
        result=str(result),
        last_message=str(last_message),
        provider=provider,
        task_type=task_type,
        pid=process.pid,
        attempt=attempt,
    )
    record.save(run_path)
    return record


def continue_worktree_worker(
    root: Path,
    config: C3xConfig,
    task: BeadSummary,
    previous: RunRecord,
    *,
    reason: str = "",
    attempt: int | None = None,
) -> RunRecord:
    attempt = attempt or _next_attempt(root, task.id)
    worktree = Path(previous.worktree)
    if not worktree.exists():
        raise AgentError(f"cannot continue {task.id}: previous worktree is missing: {worktree}")

    task_type = "worker"
    run_path = run_record_path(root, task.id)
    log_dir = run_log_dir(root, task.id, task_type, attempt)
    prompt = log_dir / "prompt.md"
    result = _worker_result_path(worktree, task.id)
    last_message = log_dir / "last-message.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        _continue_worktree_prompt(task, result, previous=previous, reason=reason),
        encoding="utf-8",
    )

    provider = _provider_for_task(config, task_type)
    command = _agent_command(config, worktree, prompt, result, last_message, task_type=task_type)
    process = subprocess.Popen(
        command,
        cwd=worktree,
        text=True,
        stdout=(prompt.parent / "stdout.log").open("w", encoding="utf-8"),
        stderr=(prompt.parent / "stderr.log").open("w", encoding="utf-8"),
        start_new_session=True,
    )
    record = RunRecord(
        task_id=task.id,
        branch=previous.branch,
        worktree=str(worktree),
        prompt=str(prompt),
        result=str(result),
        last_message=str(last_message),
        provider=provider,
        task_type=task_type,
        pid=process.pid,
        attempt=attempt,
    )
    record.save(run_path)
    return record


def start_conflict_resolver(
    root: Path,
    config: C3xConfig,
    task: BeadSummary,
    *,
    source_branch: str,
    target_branch: str,
    target_revision: str,
    original_result: str,
    attempt: int | None = None,
) -> RunRecord:
    attempt = attempt or _next_attempt(root, task.id)
    branch = _conflict_branch(task.id, task.title, attempt)
    worktree = worktrees_dir(root) / branch.replace("/", "-")
    conflicted = create_conflict_resolution_worktree(
        root,
        branch=branch,
        source_branch=source_branch,
        worktree=worktree,
    )

    task_type = "conflict_resolver"
    run_path = run_record_path(root, task.id)
    log_dir = run_log_dir(root, task.id, task_type, attempt)
    prompt = log_dir / "prompt.md"
    result = _worker_result_path(worktree, task.id)
    last_message = log_dir / "last-message.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        _conflict_resolver_prompt(
            task,
            result,
            source_branch=source_branch,
            target_branch=target_branch,
            target_revision=target_revision,
            conflicted_files=conflicted,
            original_result=original_result,
        ),
        encoding="utf-8",
    )

    provider = _provider_for_task(config, task_type)
    command = _agent_command(config, worktree, prompt, result, last_message, task_type=task_type)
    process = subprocess.Popen(
        command,
        cwd=worktree,
        text=True,
        stdout=(prompt.parent / "stdout.log").open("w", encoding="utf-8"),
        stderr=(prompt.parent / "stderr.log").open("w", encoding="utf-8"),
        start_new_session=True,
    )
    record = RunRecord(
        task_id=task.id,
        branch=branch,
        worktree=str(worktree),
        prompt=str(prompt),
        result=str(result),
        last_message=str(last_message),
        provider=provider,
        task_type=task_type,
        pid=process.pid,
        attempt=attempt,
    )
    record.save(run_path)
    return record


def run_reviewer(
    root: Path,
    config: C3xConfig,
    task: BeadSummary,
    worker_result: WorkerResult,
    *,
    record: RunRecord,
    diff_summary: str,
) -> ReviewResult:
    attempt = _next_attempt(root, task.id)
    task_type = "reviewer"
    worktree = Path(record.worktree)
    log_dir = run_log_dir(root, task.id, task_type, attempt)
    prompt = log_dir / "prompt.md"
    result = worktree / ".c3x" / f"{_safe_result_prefix(task.id)}-reviewer-result.json"
    last_message = log_dir / "last-message.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        _reviewer_prompt(
            task,
            worker_result,
            result,
            diff_summary=diff_summary,
        ),
        encoding="utf-8",
    )

    command = _agent_command(config, worktree, prompt, result, last_message, task_type=task_type)
    completed = subprocess.run(
        command,
        cwd=worktree,
        text=True,
        stdout=(prompt.parent / "stdout.log").open("w", encoding="utf-8"),
        stderr=(prompt.parent / "stderr.log").open("w", encoding="utf-8"),
        check=False,
    )
    if completed.returncode != 0:
        raise AgentError(f"reviewer exited with status {completed.returncode}")
    if not result.exists():
        recovered = _review_result_from_last_message(last_message)
        if recovered is None:
            raise AgentError(f"reviewer did not write result: {result}")
        try:
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(recovered.model_dump_json(indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        return recovered
    return ReviewResult.model_validate_json(result.read_text(encoding="utf-8"))


def _review_result_from_last_message(last_message: Path) -> ReviewResult | None:
    if not last_message.exists():
        return None
    text = last_message.read_text(encoding="utf-8", errors="replace")
    for candidate in reversed(re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, flags=re.DOTALL)):
        try:
            return ReviewResult.model_validate_json(candidate.strip())
        except Exception:
            continue
    for candidate in reversed(re.findall(r"\[[^\]]*review-result\.json\]\(([^):]+)(?::\d+)?\)", text)):
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ReviewResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _agent_command(
    config: C3xConfig,
    worktree: Path,
    prompt: Path,
    result: Path,
    last_message: Path,
    *,
    task_type: str = "worker",
    resume_session_id: str | None = None,
) -> list[str]:
    provider = _provider_for_task(config, task_type)
    prompt_content = ""
    if prompt.exists():
        prompt_content = prompt.read_text(encoding="utf-8")

    mapping = {
        "model": _model_for_task(config, provider, task_type),
        "worktree": str(worktree),
        "prompt": str(prompt),
        "prompt_content": prompt_content,
        "result": str(result),
        "last_message": str(last_message),
        "session_id": resume_session_id or "",
    }

    if provider == "antigravity":
        command_str = config.agents.antigravity_command
        executable = shlex.split(command_str)
        if not executable:
            raise AgentError("agents.antigravity_command cannot be empty")
        executable[0] = str(Path(executable[0]).expanduser())
        template = config.agents.antigravity_resume_args if resume_session_id else config.agents.antigravity_args
        args = [arg.format(**mapping) for arg in template]
        return [*executable, *args]

    command_str = config.agents.codex_command
    executable = shlex.split(command_str)
    if not executable:
        raise AgentError("agents.codex_command cannot be empty")
    executable[0] = str(Path(executable[0]).expanduser())
    template = config.agents.codex_resume_args if resume_session_id else config.agents.codex_args
    args = [arg.format(**mapping) for arg in template]
    return [*executable, *args]


def _provider_for_task(config: C3xConfig, task_type: str) -> str:
    overrides = getattr(config.agents, "provider_overrides", {})
    return overrides.get(task_type, getattr(config.agents, "provider", "codex"))


def _model_for_task(config: C3xConfig, provider: str, task_type: str) -> str:
    models = config.models_for_provider(provider)
    role = "worker" if task_type in {"worker", "conflict_resolver"} else task_type
    return getattr(models, role, models.worker)


def _next_attempt(root: Path, task_id: str) -> int:
    attempts = 0
    for path in sorted(runs_dir(root).glob("*/run.json")):
        try:
            record = RunRecord.load(path)
        except Exception:
            continue
        if record.task_id == task_id:
            attempts = max(attempts, _record_attempt(record))
    for path in worktrees_dir(root).glob(f"*{task_id}*"):
        attempts = max(attempts, _attempt_from_text(path.name) or 1)
    return attempts + 1


def _record_attempt(record: RunRecord) -> int:
    candidates = [
        record.attempt,
        _attempt_from_text(record.branch),
        _attempt_from_text(record.worktree),
        _attempt_from_text(record.result),
    ]
    return max(candidate or 1 for candidate in candidates)


def _attempt_from_text(text: str) -> int | None:
    matches = re.findall(r"(?:^|[-/])attempt-(\d+)(?:$|[-/.])", text)
    if not matches:
        return None
    return max(int(match) for match in matches)


def _attempt_branch(task_id: str, title: str, attempt: int) -> str:
    branch = task_branch(task_id, title)
    if attempt == 1:
        return branch
    return f"{branch}-attempt-{attempt}"


def _conflict_branch(task_id: str, title: str, attempt: int) -> str:
    return f"{task_branch(task_id, title)}-conflict-attempt-{attempt}"


def _worker_prompt(task: BeadSummary, result: Path) -> str:
    return f"""{_worker_prompt_preamble()}

# c3x worker task

Task: {task.id}
Title: {task.title}

Work on exactly this task in the current git worktree.

Supervisor owns task state, commits, merges, cleanup, and all Beads writes.

You may run `bd show {task.id}` to fetch the full task Description and acceptance criteria.
Do not run other Beads commands, including `bd prime`, `bd ready`, `bd update`, `bd close`,
`bd create`, `bd dolt pull`, or `bd dolt push`.

Do not run `git commit`, `git push`, `git pull`, `git merge`, or branch cleanup.
Leave changed files in the worktree. The supervisor will commit and merge them after
review.

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


def _resume_worker_prompt(
    task: BeadSummary,
    result: Path,
    *,
    previous: RunRecord,
    reason: str,
) -> str:
    return f"""{_worker_prompt_preamble()}

# c3x worker session resume

Task: {task.id}
Title: {task.title}

Continue this exact previous Codex session. The prior worker stopped before
producing a completed result, likely because of a transient external failure.

Previous attempt: {previous.attempt}
Previous status: {previous.status}
Previous outcome: {previous.outcome or "unknown"}
Reason to continue: {reason or "transient or retryable worker interruption"}

Use the existing conversation context and current worktree state. Continue from
where the previous session stopped. Do not restart the task analysis unless the
session context is insufficient or clearly stale.

Supervisor owns task state, commits, merges, cleanup, and all Beads writes.

You may run `bd show {task.id}` to fetch the full task Description and acceptance criteria.
Do not run other Beads commands, including `bd prime`, `bd ready`, `bd update`, `bd close`,
`bd create`, `bd dolt pull`, or `bd dolt push`.

Do not run `git commit`, `git push`, `git pull`, `git merge`, or branch cleanup.
Leave changed files in the worktree. The supervisor will commit and merge them after
review.

Write a structured JSON result to:
{result}

Required result shape:
```json
{{
  "task_id": "{task.id}",
  "status": "completed",
  "summary": "What changed",
  "task_kind": "feature|bug|test|refactor|docs|infra|spike",
  "attempt": {previous.attempt + 1},
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


def _continue_worktree_prompt(
    task: BeadSummary,
    result: Path,
    *,
    previous: RunRecord,
    reason: str,
) -> str:
    return f"""{_worker_prompt_preamble()}

# c3x worker worktree continuation

Task: {task.id}
Title: {task.title}

Start a fresh Codex context, but continue from the existing git worktree. The
previous worker stopped before producing a completed result, and its partial file
changes may be useful.

Previous attempt: {previous.attempt}
Previous status: {previous.status}
Previous outcome: {previous.outcome or "unknown"}
Reason to continue: {reason or "retry requested"}

First inspect `git status`, relevant diffs, existing tests, and any current
partial implementation. Preserve useful existing edits. Replace them only when
they are clearly wrong for this task.

Supervisor owns task state, commits, merges, cleanup, and all Beads writes.

You may run `bd show {task.id}` to fetch the full task Description and acceptance criteria.
Do not run other Beads commands, including `bd prime`, `bd ready`, `bd update`, `bd close`,
`bd create`, `bd dolt pull`, or `bd dolt push`.

Do not run `git commit`, `git push`, `git pull`, `git merge`, or branch cleanup.
Leave changed files in the worktree. The supervisor will commit and merge them after
review.

Write a structured JSON result to:
{result}

Required result shape:
```json
{{
  "task_id": "{task.id}",
  "status": "completed",
  "summary": "What changed",
  "task_kind": "feature|bug|test|refactor|docs|infra|spike",
  "attempt": {previous.attempt + 1},
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


def _conflict_resolver_prompt(
    task: BeadSummary,
    result: Path,
    *,
    source_branch: str,
    target_branch: str,
    target_revision: str,
    conflicted_files: list[str],
    original_result: str,
) -> str:
    files = "\n".join(f"- {path}" for path in conflicted_files) or "- No currently unmerged files"
    return f"""{_worker_prompt_preamble()}

# c3x merge conflict resolver

Use the `flow-conflict-resolver` skill.

Task: {task.id}
Title: {task.title}

Resolve the merge conflict for this task in the current git worktree.

Source branch: {source_branch}
Target branch: {target_branch}
Target revision: {target_revision}

Conflicted files:
{files}

Original worker result:
```json
{original_result}
```

Supervisor owns task state, commits, merges, cleanup, and all Beads writes.

You may run `bd show {task.id}` to fetch the full task Description and acceptance criteria.
Do not run other Beads commands, including `bd prime`, `bd ready`, `bd update`, `bd close`,
`bd create`, `bd dolt pull`, or `bd dolt push`.

Do not run `git commit`, `git push`, `git pull`, `git merge`, or branch cleanup.
Leave changed files in the worktree. The supervisor will commit and merge them after
review.

Resolve only the merge conflict. Preserve the original task intent and the target
branch behavior. Do not make unrelated changes.

Write a structured JSON result to:
{result}

Required result shape:
```json
{{
  "task_id": "{task.id}",
  "status": "completed",
  "summary": "How the conflict was resolved",
  "task_kind": "merge-conflict",
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

If the conflict cannot be resolved safely, set `status` to `blocked`, use
`blocker_category` `merge-conflict`, and describe the exact unresolved files.
"""


def _worker_prompt_preamble() -> str:
    return f"""/status

{caveman_mode_text()}

Before task work, preserve the `/status` output in the worker log/transcript.
If this Codex mode does not execute slash commands from prompts, continue normally
after this status probe.

Before editing files, read and follow the root `AGENTS.md` plus any nested
`AGENTS.md` files that apply to the files you touch.
"""


def _reviewer_prompt(
    task: BeadSummary,
    worker_result: WorkerResult,
    result: Path,
    *,
    diff_summary: str,
) -> str:
    requirements = _task_requirements(task)
    return f"""/review

{caveman_mode_text()}

Use the `flow-reviewer` skill.

# c3x reviewer task

Task: {task.id}
Title: {task.title}

Review the current worktree branch. Decide if this work may land.

Supervisor owns all Beads writes, follow-up task creation, commits, merges, and cleanup.
Do not run `bd update`, `bd close`, `bd create`, `bd dep`, `git commit`, `git push`,
`git pull`, `git merge`, or branch cleanup.

Requirements to check:
{requirements}

Worker result:
```json
{worker_result.model_dump_json(indent=2)}
```

Diff summary:
```text
{diff_summary}
```

Check each requirement explicitly. Block landing when any requirement is unmet or unclear,
verification is missing/failed/skipped without justification, scope is wrong, or evidence is
not enough.

Write a structured JSON review result to:
{result}

Required result shape:
```json
{{
  "task_id": "{task.id}",
  "status": "approved",
  "summary": "Review decision and evidence",
  "requirements": [
    {{"requirement": "specific requirement", "status": "met|unmet|unclear", "evidence": "files/tests/behavior checked"}}
  ],
  "issues": [
    {{"title": "Fix concrete problem", "description": "Issue evidence and expected cleanup", "severity": "critical|high|medium|low"}}
  ]
}}
```

Use `status: "blocked"` when `issues` is non-empty.
"""


def _task_requirements(task: BeadSummary) -> str:
    lines = [f"- Title: {task.title}"]
    if task.description:
        lines.append("- Description:\n" + _indent_block(task.description))
    if task.acceptance:
        lines.append("- Acceptance criteria:\n" + _indent_block(task.acceptance))
    if task.notes:
        lines.append("- Existing notes:\n" + _indent_block(task.notes))
    return "\n".join(lines)


def _indent_block(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.strip().splitlines())
