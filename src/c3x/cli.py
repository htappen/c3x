from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable, Literal

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table

from c3x.agent import (
    AgentError,
    _next_attempt,
    continue_worktree_worker,
    resume_session_worker,
    run_reviewer,
    start_conflict_resolver,
    start_worker,
)
from c3x.beads import Beads, BeadsError, BeadSummary
from c3x.config import FLOW_DIR, load_config, write_default_config
from c3x.gitops import (
    GitError,
    GitMergeConflict,
    branch_diff_summary,
    commit_parents,
    commit_ledger_changes,
    commit_subject,
    commit_worktree_changes,
    current_branch,
    delete_branch,
    ensure_rewrite_safe,
    history_has_subject,
    is_ancestor,
    merge_branch,
    remove_worktree,
    rev_parse,
    squash_head_to,
    worktree_branches,
    worktree_has_changes,
)
from c3x.metrics import collect_metrics
from c3x.paths import activity_path, pause_path, result_path, run_record_path, stuck_notice_path
from c3x.schema import ReviewIssue, ReviewResult, RunRecord, WorkerResult
from c3x.verify import run_verification


app = typer.Typer(
    name="c3x",
    help="Local agentic coding supervisor for Codex and Beads.",
    no_args_is_help=True,
)
console = Console()
RetryMode = Literal["session", "worktree", "fresh"]


@dataclass(frozen=True)
class CleanupAction:
    task_id: str
    run_dir: Path
    worktree: Path
    branch: str
    reason: str
    remove_run_dir: bool = False
    repair_merge: bool = False
    repair_run_record: bool = False


@dataclass(frozen=True)
class SquashPlan:
    task_id: str
    base: str
    commits: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class UnstickCandidate:
    task_id: str
    action: str
    reason: str
    record_status: str
    bead_status: str | None
    verification_issues: tuple[str, ...] = ()
    cheap_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowRow:
    state: str
    stage: str
    count: int
    detail: str


@dataclass(frozen=True)
class StatusSnapshot:
    config: object
    active_items: list[BeadSummary]
    ready_items: list[BeadSummary]
    canonical_records: list[RunRecord]
    live_records: list[RunRecord]


def _root() -> Path:
    return Path.cwd()


def _beads(root: Path) -> Beads:
    return Beads(root=root)


@app.command()
def init(
    skip_beads: Annotated[
        bool,
        typer.Option("--skip-beads", help="Create .flow config without running `bd init`."),
    ] = False,
) -> None:
    """Initialize c3x metadata and a project-local Beads ledger."""
    root = _root()
    config_path = write_default_config(root)
    for directory in ("runs", "agents", "worktrees"):
        (root / FLOW_DIR / directory).mkdir(parents=True, exist_ok=True)

    if not skip_beads:
        try:
            _beads(root).init()
        except BeadsError as exc:
            raise typer.Exit(_error(str(exc))) from exc

    console.print(f"[green]Initialized c3x[/green] at {root}")
    console.print(f"Config: {config_path}")


@app.command()
def add(
    title: Annotated[str, typer.Argument(help="Raw idea or feedback to add to the inbox.")],
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="Optional detail for the inbox item."),
    ] = None,
    priority: Annotated[
        int,
        typer.Option("--priority", "-p", min=0, max=4, help="Beads priority, 0 highest."),
    ] = 2,
    validate: Annotated[
        bool,
        typer.Option(
            "--validate/--no-validate",
            help="Validate the feedback synchronously and ask clarification questions before returning.",
        ),
    ] = True,
) -> None:
    """Add raw feedback to the Beads-backed c3x inbox."""
    root = _root()
    beads = _beads(root)
    try:
        item = beads.create_inbox_item(title, description=description, priority=priority)
        item_id = str(item.get("id", "<unknown>"))
        if validate and item_id != "<unknown>":
            _validate_item_interactively(root, beads, item_id)
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc

    item_id = item.get("id", "<unknown>")
    console.print(f"[green]Added[/green] {item_id}: {title}")


@app.command()
def inbox() -> None:
    """Show open c3x inbox items."""
    root = _root()
    try:
        items = [
            item
            for item in _beads(root).list_active()
            if {"flow", "inbox", "idea"}.issubset(set(item.labels))
        ]
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    _print_items("Inbox", items)


@app.command()
def status(
    watch: Annotated[bool, typer.Option("--watch", help="Refresh status in place.")] = False,
    interval: Annotated[int, typer.Option("--interval", min=1, help="Watch refresh seconds.")] = 2,
) -> None:
    """Show the current c3x project status."""
    root = _root()
    try:
        view = _build_status_view(root)
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    if not watch:
        console.print(view)
        return
    with _status_live(view) as live:
        while True:
            time.sleep(interval)
            try:
                live.update(_build_status_view(root))
            except BeadsError as exc:
                raise typer.Exit(_error(str(exc))) from exc


@app.command()
def answer(
    task_id: Annotated[str, typer.Argument(help="Question or task id to answer.")],
    text: Annotated[str, typer.Argument(help="Answer text to append to the bead.")],
) -> None:
    """Record a human answer on a question bead."""
    root = _root()
    beads = _beads(root)
    try:
        question = beads.show(task_id)
        beads.add_note(task_id, f"Human answer: {text}")
        beads.add_labels(task_id, ["answered"])
        beads.remove_labels(task_id, ["question", "needs-human-clarification"])
        blocked_item = _blocked_item_id(question)
        if blocked_item:
            beads.add_note(blocked_item, f"Clarification from {task_id}: {text}")
            beads.add_labels(blocked_item, ["clarified"])
        beads.close(task_id, "Answered human clarification")
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Answered[/green] {task_id}")


@app.command()
def questions() -> None:
    """Show outstanding human clarification questions."""
    root = _root()
    try:
        items = _open_questions(_beads(root))
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    _print_items("Questions", items)


@app.command()
def blocked() -> None:
    """Show c3x flow tasks marked blocked, with the likely blocker reason."""
    root = _root()
    try:
        items = _with_labels(_beads(root).list_active(), {"flow", "blocked"})
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    _print_blocked_items(items)


@app.command()
def clarify() -> None:
    """Answer outstanding human clarification questions in a terminal chat loop."""
    root = _root()
    beads = _beads(root)
    try:
        _clarify_questions(beads)
        _plan_inbox(root, beads)
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc


@app.command()
def run(
    once: Annotated[bool, typer.Option("--once", help="Run one supervisor tick and exit.")] = False,
    dispatch: Annotated[bool, typer.Option("--dispatch", help="Start ready tasks after planning.")] = False,
    interval: Annotated[int, typer.Option("--interval", min=1, help="Loop sleep seconds.")] = 5,
) -> None:
    """Run the c3x supervisor loop."""
    root = _root()
    _write_activity(root, "starting supervisor loop")
    with _status_live(_build_status_view(root)) as live:
        while True:
            if pause_path(root).exists():
                _write_activity(root, "paused")
                console.print("[yellow]c3x is paused.[/yellow]")
                live.update(_build_status_view(root))
                if once:
                    return
                time.sleep(interval)
                continue
            _supervisor_tick(root, dispatch=dispatch)
            live.update(_build_status_view(root))
            if once:
                return
            time.sleep(interval)


@app.command()
def watch(
    interval: Annotated[int, typer.Option("--interval", min=1, help="Loop sleep seconds.")] = 5,
    review: Annotated[
        bool,
        typer.Option("--review/--no-review", help="Automatically review completed worker results."),
    ] = True,
    land: Annotated[
        bool,
        typer.Option("--land/--no-land", help="Automatically land reviewed work into the current root branch."),
    ] = True,
    cleanup_done: Annotated[
        bool,
        typer.Option("--cleanup/--no-cleanup", help="Automatically remove landed worktrees and branches."),
    ] = True,
    resolve_conflicts: Annotated[
        bool,
        typer.Option(
            "--resolve-conflicts/--no-resolve-conflicts",
            help="Automatically start conflict resolver agents for merge-conflict land blockers.",
        ),
    ] = True,
) -> None:
    """Run the autonomous c3x watch loop."""
    root = _root()
    _write_activity(root, "starting autonomous watch loop")
    with _status_live(_build_status_view(root)) as live:
        while True:
            if pause_path(root).exists():
                _write_activity(root, "paused")
                console.print("[yellow]c3x is paused.[/yellow]")
                live.update(_build_status_view(root))
                time.sleep(interval)
                continue
            _supervisor_tick(
                root,
                dispatch=True,
                review=review,
                land=land,
                cleanup_done=cleanup_done,
                resolve_conflicts=resolve_conflicts,
            )
            live.update(_build_status_view(root))
            time.sleep(interval)


@app.command()
def start(
    task_id: Annotated[str, typer.Argument(help="Ready task id to start.")],
) -> None:
    """Start one worker in an isolated git worktree."""
    root = _root()
    _warn_if_risky_flow_branch(root)
    config = load_config(root)
    beads = _beads(root)
    try:
        task = beads.show(task_id)
        record = start_worker(root, config, task)
        beads.set_status(task_id, "in_progress")
        beads.add_labels(task_id, ["flow", "running", f"attempt-{record.attempt}"])
        beads.remove_labels(task_id, ["ready", "reviewing", "blocked"])
        beads.add_note(task_id, f"c3x attempt {record.attempt} started")
    except (AgentError, BeadsError, GitError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Started[/green] {task_id}")
    console.print(f"Worktree: {record.worktree}")


@app.command()
def retry(
    task_id: Annotated[str | None, typer.Argument(help="Task id to retry.")] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Retry all currently blocked flow tasks."),
    ] = False,
    resume_session: Annotated[
        bool,
        typer.Option(
            "--resume-session",
            help="Resume the previous Codex session. This is the default when no retry mode flag is passed.",
        ),
    ] = False,
    continue_worktree: Annotated[
        bool,
        typer.Option(
            "--continue-worktree",
            help="Start a fresh Codex context in the previous attempt worktree.",
        ),
    ] = False,
    fresh: Annotated[
        bool,
        typer.Option(
            "--fresh",
            help="Start a fresh Codex context in a new attempt worktree.",
        ),
    ] = False,
) -> None:
    """Retry blocked or stale work, resuming the previous Codex session by default."""
    root = _root()
    _warn_if_risky_flow_branch(root)
    config = load_config(root)
    beads = _beads(root)
    try:
        retry_mode = _retry_mode_from_flags(
            resume_session=resume_session,
            continue_worktree=continue_worktree,
            fresh=fresh,
        )
        _import_finished_results(root, beads)
        task_ids = _retry_task_ids(beads, task_id=task_id, all_tasks=all_tasks)
        for item_id in task_ids:
            record, mode_used = _retry_task(root, config, beads, item_id, retry_mode=retry_mode)
            action = {
                "session": "Resumed session",
                "worktree": "Continued worktree",
                "fresh": "Retried fresh",
            }[mode_used]
            console.print(f"[green]{action}[/green] {item_id} as attempt {record.attempt}")
            console.print(f"Worktree: {record.worktree}")
    except (AgentError, BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc


@app.command()
def resolve_conflict(
    task_id: Annotated[str | None, typer.Argument(help="Merge-conflict-blocked task id to resolve.")] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Resolve all currently merge-conflict-blocked flow tasks."),
    ] = False,
) -> None:
    """Start a conflict resolver worker for merge-conflict-blocked work."""
    root = _root()
    _warn_if_risky_flow_branch(root)
    config = load_config(root)
    beads = _beads(root)
    try:
        _import_finished_results(root, beads)
        task_ids = _conflict_task_ids(beads, task_id=task_id, all_tasks=all_tasks)
        for item_id in task_ids:
            record = _resolve_conflict_task(root, config, beads, item_id)
            console.print(f"[green]Resolving conflict[/green] {item_id} as attempt {record.attempt}")
            console.print(f"Worktree: {record.worktree}")
    except (AgentError, BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc


@app.command()
def squash(
    task_id: Annotated[str | None, typer.Argument(help="Landed task id to squash.")] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Squash all eligible landed task commits at the current branch tip."),
    ] = False,
) -> None:
    """Squash c3x-generated commits for landed work."""
    root = _root()
    try:
        ensure_rewrite_safe(root)
        plans = _squash_plans(root, task_id=task_id, all_tasks=all_tasks)
        if not plans:
            console.print("[green]Nothing to squash.[/green]")
            return
        for plan in plans:
            squash_head_to(root, plan.base, plan.message)
            console.print(f"[green]Squashed[/green] {plan.task_id}: {len(plan.commits)} commits")
    except (GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc


@app.command()
def agents() -> None:
    """List known local worker runs."""
    root = _root()
    records = _run_records(root)
    table = Table(title="c3x agents")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("PID", justify="right")
    table.add_column("Branch")
    for record in records:
        table.add_row(record.task_id, record.status, "" if record.pid is None else str(record.pid), record.branch)
    console.print(table)


@app.command()
def metrics(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Summarize agent outcomes, retries, unfinished work, and blockers."""
    data = collect_metrics(_root())
    if json_output:
        console.print_json(data=data)
        return
    table = Table(title="c3x metrics")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total tasks attempted", str(data["total_tasks"]))
    table.add_row("Total runs", str(data["total_runs"]))
    table.add_row("Rejected or blocked tasks", str(data["rejected_or_blocked"]))
    table.add_row("Unfinished tasks", str(data["unfinished"]))
    table.add_row("Avg attempts to land", str(data["avg_attempts_to_land"]))
    console.print(table)
    _print_counter("Run outcomes", data["outcomes"])
    _print_counter("Task kinds", data["task_kinds"])
    _print_counter("Blocker categories", data["blocker_categories"])


@app.command()
def verify(
    command: Annotated[
        list[str] | None,
        typer.Argument(help="Optional verification command. Defaults to .flow/config.yml verify list."),
    ] = None,
) -> None:
    """Run configured project verification commands."""
    root = _root()
    config = load_config(root)
    commands = command or config.verify
    if not commands:
        console.print("[yellow]No verification commands configured.[/yellow]")
        return
    results = run_verification(root, commands)
    _print_verification(results)
    if any(result.status == "failed" for result in results):
        raise typer.Exit(1)


@app.command()
def review(
    task_id: Annotated[str, typer.Argument(help="Task id to review.")],
) -> None:
    """Review a completed worker result and mark it ready to land."""
    root = _root()
    try:
        beads = _beads(root)
        result = _load_worker_result(root, task_id)
        _review_result(result)
        record = _load_repaired_current_run_record(root, task_id)
        item = beads.show(task_id)
        _commit_worktree_before_review(record)
        review_result = run_reviewer(
            root,
            load_config(root),
            item,
            result,
            record=record,
            diff_summary=branch_diff_summary(root, record.branch),
        )
        _apply_review_result(root, beads, item, result, review_result, record=record)
    except (AgentError, BeadsError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Reviewed[/green] {task_id}")


@app.command()
def land(
    task_id: Annotated[str | None, typer.Argument(help="Reviewed task id to merge.")] = None,
    land_all: Annotated[
        bool,
        typer.Option("--all", help="Merge all reviewed tasks, dependencies then oldest worktree first."),
    ] = False,
    cleanup_done: Annotated[
        bool,
        typer.Option("--cleanup/--no-cleanup", help="Remove the landed worktree and branch after merge."),
    ] = True,
) -> None:
    """Merge a reviewed task branch and close the bead."""
    if land_all == (task_id is not None):
        raise typer.Exit(_error("pass a task id or --all"))
    root = _root()
    _warn_if_risky_flow_branch(root)
    if worktree_has_changes(root, ignored_prefixes=(".c3x/", ".flow/")):
        try:
            target = "all reviewed tasks" if land_all else f"task {task_id}"
            commit_worktree_changes(root, f"Save local changes before landing {target}")
        except GitError as exc:
            raise typer.Exit(_error(f"root worktree has uncommitted changes that could not be saved: {exc}"))
    if land_all:
        try:
            _land_all(root, cleanup_done=cleanup_done)
        except BeadsError as exc:
            raise typer.Exit(_error(str(exc))) from exc
        return
    try:
        assert task_id is not None
        record = _load_repaired_current_run_record(root, task_id)
        if record.status != "reviewed":
            raise ValueError(f"{task_id} is not reviewed")
        beads = _beads(root)
        _land_record(root, beads, record, cleanup_done=cleanup_done, close_note="Landed by c3x")
    except GitMergeConflict as exc:
        beads = _beads(root)
        _mark_land_blocked(beads, task_id, exc)
        raise typer.Exit(_error(str(exc))) from exc
    except (BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Landed[/green] {task_id}")
    if cleanup_done:
        console.print(f"[green]Cleaned[/green] {task_id}")


def _land_all(root: Path, *, cleanup_done: bool) -> None:
    beads = _beads(root)
    records: list[RunRecord] = []
    blocked = 0
    for item in _ready_to_land_items(beads.list_active()):
        try:
            record = _load_repaired_current_run_record(root, item.id)
            if record.status == "reviewed":
                records.append(record)
        except (GitError, ValueError) as exc:
            blocked += 1
            _mark_land_error(beads, item.id, exc)
            console.print(f"[yellow]Land blocked[/yellow] {item.id}: {exc}")
    records = _order_land_records(beads, records)
    landed = 0
    for record in records:
        try:
            _land_record(root, beads, record, cleanup_done=cleanup_done, close_note="Landed by c3x")
            landed += 1
            console.print(f"[green]Landed[/green] {record.task_id}")
            if cleanup_done:
                console.print(f"[green]Cleaned[/green] {record.task_id}")
        except GitMergeConflict as exc:
            blocked += 1
            _mark_land_blocked(beads, record.task_id, exc)
            console.print(f"[yellow]Land blocked[/yellow] {record.task_id}: {exc}")
        except (BeadsError, GitError, ValueError) as exc:
            blocked += 1
            _mark_land_error(beads, record.task_id, exc)
            console.print(f"[yellow]Land blocked[/yellow] {record.task_id}: {exc}")
    console.print(f"Landed {landed}; blocked {blocked}")
    if blocked:
        raise typer.Exit(1)


def _order_land_records(beads: Beads, records: list[RunRecord]) -> list[RunRecord]:
    remaining = {record.task_id: record for record in records}
    blockers = {
        task_id: {
            str(dependency.get("depends_on_id"))
            for dependency in beads.dependencies(task_id, direction="down", dep_type="blocks")
            if dependency.get("depends_on_id") in remaining
        }
        for task_id in remaining
    }
    ordered: list[RunRecord] = []
    while remaining:
        ready = [record for task_id, record in remaining.items() if not blockers[task_id].intersection(remaining)]
        if not ready:
            ready = list(remaining.values())
        ready.sort(key=lambda record: (record.started_at, record.task_id))
        for record in ready:
            ordered.append(record)
            remaining.pop(record.task_id)
    return ordered


def _land_record(
    root: Path,
    beads: Beads,
    record: RunRecord,
    *,
    cleanup_done: bool,
    close_note: str,
) -> None:
    landing_root, landing_branch, lands_in_ancestor = _landing_target(root, beads, record)
    if not lands_in_ancestor and (landing_branch == record.branch or landing_branch.startswith("c3x/")):
        raise GitError(
            f"refusing to land {record.task_id} into task branch `{landing_branch}`; "
            "run c3x land from the project landing branch"
        )
    commit_worktree_changes(Path(record.worktree), f"Complete c3x task {record.task_id}")
    if landing_branch != record.branch:
        merge_branch(landing_root, record.branch)
    beads.close(record.task_id, close_note)
    beads.add_labels(record.task_id, ["landed"])
    commit_ledger_changes(landing_root, f"Close c3x task {record.task_id}")
    record.status = "landed"
    record.outcome = "landed"
    record.finished_at = _now()
    record.landing_branch = landing_branch
    record.landed_revision = rev_parse(landing_root, landing_branch)
    record.save(run_record_path(root, record.task_id))
    if cleanup_done and Path(record.worktree) != landing_root:
        remove_worktree(root, Path(record.worktree), force=True)
        if landing_branch != record.branch:
            delete_branch(root, record.branch)


def _landing_target(root: Path, beads: Beads, record: RunRecord) -> tuple[Path, str, bool]:
    ancestor_id = _blocking_ancestor_id(beads, record.task_id)
    if ancestor_id and ancestor_id != record.task_id:
        ancestor = _current_run_record(root, ancestor_id)
        if ancestor is not None and Path(ancestor.worktree).exists():
            landing_root = Path(ancestor.worktree)
            return landing_root, current_branch(landing_root), True
    return root, current_branch(root), False


def _blocking_ancestor_id(beads: Beads, task_id: str) -> str | None:
    seen = {task_id}
    ancestor_id: str | None = None
    current_id = task_id
    while True:
        try:
            current = beads.show(current_id)
        except (BeadsError, KeyError):
            return ancestor_id
        if not isinstance(current, BeadSummary):
            return ancestor_id
        parent_id = _review_fix_parent_id(beads, current)
        if not parent_id or parent_id in seen:
            return ancestor_id
        seen.add(parent_id)
        ancestor_id = parent_id
        current_id = parent_id


@app.command()
def cleanup(
    task_id: Annotated[str | None, typer.Argument(help="Optional task id to clean up.")] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show cleanup candidates without removing anything."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Force-remove dirty stale worktrees and unmerged stale branches."),
    ] = False,
    repair_beads: Annotated[
        bool,
        typer.Option(
            "--repair-beads",
            help="Compact oversized Beads payloads that make bd updates fail.",
        ),
    ] = False,
) -> None:
    """Remove landed task worktrees and superseded stale attempts."""
    root = _root()
    try:
        beads: Beads | None = None
        try:
            beads = _beads(root)
        except BeadsError:
            if repair_beads:
                raise
        if repair_beads:
            assert beads is not None
            _repair_large_beads_payloads(root, beads, task_id=task_id, dry_run=dry_run)
        try:
            reconciled = _cleanup_reconcile_labels(root, beads, task_id=task_id, dry_run=dry_run) if beads else 0
        except BeadsError:
            if repair_beads:
                raise
            reconciled = 0
        actions = _cleanup_actions(root, task_id=task_id, require_task_cleanup=not repair_beads and not reconciled)
        if not actions:
            if not reconciled:
                console.print("[green]Nothing to clean.[/green]")
            return
        for action in actions:
            if dry_run:
                verb = "repair" if action.repair_run_record else "clean"
                target = f"{action.task_id} ({action.run_dir.name})" if action.repair_run_record else action.task_id
                console.print(f"[yellow]Would {verb}[/yellow] {action.reason}: {target}")
                continue
            if action.repair_merge and not _confirm_repair_merge(root, action):
                console.print(f"[yellow]Skipped[/yellow] {action.task_id}")
                continue
            _run_cleanup_action(root, action, force=force)
            verb = "Repaired" if action.repair_run_record else "Cleaned"
            target = f"{action.task_id} ({action.run_dir.name})" if action.repair_run_record else action.task_id
            console.print(f"[green]{verb}[/green] {action.reason}: {target}")
    except (BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc


@app.command(name="kill")
def kill_workers(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show recorded live workers without killing them.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Send SIGKILL instead of SIGTERM.")] = False,
    all_runs: Annotated[
        bool,
        typer.Option("--all", help="Also inspect non-running run records for live recorded PIDs."),
    ] = False,
) -> None:
    """Kill recorded c3x worker processes for this project."""
    root = _root()
    records = [
        record
        for record in _run_records(root)
        if record.pid is not None and (all_runs or record.status == "running") and _process_is_running(record.pid)
    ]
    if not records:
        console.print("[green]No live c3x worker processes found.[/green]")
        return
    signal_name = "SIGKILL" if force else "SIGTERM"
    for record in records:
        pids = _worker_process_targets(record.pid)
        if dry_run:
            console.print(f"[yellow]Would send {signal_name}[/yellow] {record.task_id}: {', '.join(map(str, pids))}")
            continue
        killed = _kill_worker_process_tree(record.pid, force=force)
        console.print(f"[green]Sent {signal_name}[/green] {record.task_id}: {', '.join(map(str, killed))}")
    if not dry_run:
        console.print("Restart `c3x watch`; dead running attempts will be imported or restarted on the next tick.")


@app.command()
def unstick(
    task_id: Annotated[str | None, typer.Argument(help="Optional task id to inspect or repair.")] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Show repair candidates without applying changes."),
    ] = True,
    fix: Annotated[bool, typer.Option("--fix", help="Apply high-confidence repairs.")] = False,
    verify_mode: Annotated[
        str,
        typer.Option("--verify", help="Verification mode: cheap or none."),
    ] = "cheap",
    accept_verification_gaps: Annotated[
        bool,
        typer.Option("--accept-verification-gaps", help="Repair even when cheap verification reports gaps."),
    ] = False,
) -> None:
    """Detect and repair stale c3x worker/Beads state."""
    root = _root()
    if fix:
        dry_run = False
    if not dry_run and worktree_has_changes(root, ignored_prefixes=(".c3x/", ".flow/")):
        try:
            commit_worktree_changes(root, "Save local changes before unsticking task")
        except GitError as exc:
            raise typer.Exit(_error(f"root worktree has uncommitted changes that could not be saved: {exc}"))
    if verify_mode not in {"cheap", "none"}:
        raise typer.Exit(_error("--verify must be cheap or none"))
    try:
        beads = _beads(root)
        candidate_verify_mode = "recorded" if dry_run and verify_mode == "cheap" else verify_mode
        candidates = _unstick_candidates(root, beads, task_id=task_id, verify_mode=candidate_verify_mode)
        if not candidates:
            console.print("[green]No stuck c3x state detected.[/green]")
            return
        _print_unstick_candidates(candidates, fix=not dry_run)
        if dry_run:
            console.print("[yellow]Dry run only.[/yellow] Re-run with --fix to apply eligible repairs.")
            return
        seen: set[tuple[str, str]] = set()
        while candidates:
            repaired = False
            for candidate in candidates:
                key = (candidate.task_id, candidate.action)
                if key in seen:
                    continue
                seen.add(key)
                if candidate.verification_issues and not accept_verification_gaps:
                    console.print(f"[yellow]Skipped[/yellow] {candidate.task_id}: cheap verification has gaps")
                    continue
                _apply_unstick_candidate(root, beads, candidate)
                console.print(f"[green]Repaired[/green] {candidate.task_id}: {candidate.action}")
                repaired = True
            if not repaired:
                break
            if task_id is not None:
                break
            candidates = [
                candidate
                for candidate in _unstick_candidates(root, beads, task_id=None, verify_mode=verify_mode)
                if candidate.action == "close-review-resolved"
            ]
    except (BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc


@app.command()
def pause() -> None:
    """Pause supervisor dispatch/import loops."""
    path = pause_path(_root())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("paused\n", encoding="utf-8")
    console.print("[yellow]Paused c3x[/yellow]")


@app.command()
def resume() -> None:
    """Resume supervisor dispatch/import loops."""
    path = pause_path(_root())
    if path.exists():
        path.unlink()
    console.print("[green]Resumed c3x[/green]")


@app.command()
def critic() -> None:
    """Create improvement tasks from repeated blocked work."""
    root = _root()
    beads = _beads(root)
    try:
        blocked = _with_labels(beads.list_active(), {"flow", "blocked"})
        if len(blocked) < 2:
            console.print("[green]No repeated blocked pattern detected.[/green]")
            return
        existing = [
            item
            for item in beads.list_active()
            if {"flow", "critic"}.issubset(set(item.labels))
            and "blocked tasks need investigation" in item.title.lower()
        ]
        if existing:
            console.print(f"[yellow]Critic task already exists:[/yellow] {existing[0].id}")
            return
        created = beads.create_task(
            "Blocked tasks need investigation",
            description=(
                f"c3x critic found {len(blocked)} blocked tasks.\n\n"
                "Investigate whether missing fixtures, setup docs, verification commands, "
                "or scope rules are repeatedly slowing agents down."
            ),
            labels=["flow", "critic", "ready"],
            issue_type="task",
            priority=1,
        )
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Created critic task[/green] {created.get('id', '<unknown>')}")


def _print_items(title: str, items: list[BeadSummary]) -> None:
    table = Table(title=title)
    table.add_column("ID")
    table.add_column("P", justify="right")
    table.add_column("Status")
    table.add_column("Title")
    for item in items:
        table.add_row(
            item.id,
            "" if item.priority is None else str(item.priority),
            item.status or "",
            item.title,
        )
    console.print(table)


def _print_blocked_items(items: list[BeadSummary]) -> None:
    if not items:
        console.print("[green]No blocked c3x flow tasks.[/green]")
        return
    table = Table(title="Blocked")
    table.add_column("ID", no_wrap=True)
    table.add_column("P", justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Reason", ratio=2)
    table.add_column("Title")
    for item in items:
        table.add_row(
            item.id,
            "" if item.priority is None else str(item.priority),
            item.status or "",
            _blocked_reason(item),
            item.title,
        )
    console.print(table)


def _status_live(view: Group) -> Live:
    return Live(view, console=console, refresh_per_second=4, screen=True)


def _build_status_view(root: Path) -> Group:
    snapshot = _status_snapshot(root)
    return Group(
        _build_supervisor_table(root),
        _build_status_table(root, snapshot),
        _build_unstick_table(snapshot),
        _build_codex_status_table(snapshot),
        _build_workers_table(snapshot),
    )


def _status_snapshot(root: Path) -> StatusSnapshot:
    config = load_config(root)
    beads = _beads(root)
    active_items = beads.list_active()
    ready_items = [item for item in active_items if "ready" in item.labels]
    canonical_records = _canonical_run_records(root)
    live_records = _live_worker_records(root, canonical_records=canonical_records)
    return StatusSnapshot(
        config=config,
        active_items=active_items,
        ready_items=ready_items,
        canonical_records=canonical_records,
        live_records=live_records,
    )


def _build_supervisor_table(root: Path) -> Table:
    activity = _read_activity(root)
    events = _activity_events(activity)

    table = Table(title="c3x supervisor")
    table.add_column("Age")
    table.add_column("Event")
    table.add_column("Detail")
    if not events:
        table.add_row("", "idle", "no supervisor activity recorded")
        return table
    for event in events[:10]:
        updated_at = event.get("updated_at", "")
        table.add_row(_age_label(updated_at) if updated_at else "", event.get("event", ""), event.get("detail", ""))
    return table


def _build_activity_table(root: Path) -> Table:
    return _build_supervisor_table(root)


def _build_workers_table(snapshot: StatusSnapshot) -> Table:
    table = Table(title="c3x workers")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("PID", justify="right")
    table.add_column("Age")
    table.add_column("Latest")
    workers = snapshot.live_records
    if not workers:
        table.add_row("-", "idle", "", "", "")
        return table
    for record in workers:
        latest = _one_line(_read_tail(Path(record.last_message), max_chars=180))
        table.add_row(
            record.task_id,
            record.status,
            "" if record.pid is None else str(record.pid),
            _age_label(record.started_at),
            latest,
        )
    return table


def _build_codex_status_table(snapshot: StatusSnapshot) -> Table:
    config = snapshot.config
    provider = getattr(getattr(config, "agents", None), "provider", "codex")
    active_ids = {item.id for item in _with_labels(snapshot.active_items, {"flow"})}
    table = Table(title=f"{provider} /status")
    table.add_column("Task")
    table.add_column("Latest")
    rows = 0
    for record in _provider_status_records(snapshot, active_ids=active_ids):
        status = _codex_status_for_record(record) or _worker_status_fallback(record)
        if status:
            table.add_row(record.task_id, status)
            rows += 1
    if rows == 0:
        table.add_row("-", "no captured /status output")
    return table


def _provider_status_records(snapshot: StatusSnapshot, *, active_ids: set[str]) -> list[RunRecord]:
    records: list[RunRecord] = []
    seen: set[str] = set()
    for record in snapshot.live_records:
        if record.task_id not in active_ids:
            continue
        records.append(record)
        seen.add(record.task_id)
    for record in snapshot.canonical_records:
        if record.task_id not in active_ids:
            continue
        if record.task_id in seen or record.status not in {"running", "blocked", "failed"}:
            continue
        if _codex_status_for_record(record) or _worker_status_fallback(record):
            records.append(record)
            seen.add(record.task_id)
    return records[:10]


def _codex_status_for_record(record: RunRecord) -> str:
    run_dir = Path(record.prompt).parent
    text = "\n".join(
        [
            _read_tail(Path(record.last_message), max_chars=4000),
            _read_tail(run_dir / "stderr.log", max_chars=12000),
            _read_tail(run_dir / "stdout.log", max_chars=12000),
        ]
    )
    return _extract_codex_status(text)


def _worker_status_fallback(record: RunRecord) -> str:
    run_dir = Path(record.prompt).parent
    text = "\n".join(
        [
            _read_tail(Path(record.last_message), max_chars=4000),
            _read_tail(run_dir / "stderr.log", max_chars=12000),
            _read_tail(run_dir / "stdout.log", max_chars=12000),
        ]
    )
    if _has_usage_limit_evidence(text):
        return "no /status captured; Codex usage limit evidence in worker logs"
    if "rate limit" in text.lower() or "429" in text:
        return "no /status captured; Codex rate limit evidence in worker logs"
    if text.strip():
        return "no /status captured; latest worker output exists"
    return ""


def _extract_codex_status(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    marker = -1
    for index, line in enumerate(lines):
        normalized = line.lower().strip()
        if (
            normalized == "/status"
            or normalized.startswith("codex /status")
            or normalized.startswith("antigravity /status")
            or normalized.startswith("agy /status")
        ):
            marker = index
    if marker < 0:
        return ""
    status_lines: list[str] = []
    for line in lines[marker + 1 :]:
        if not line:
            if status_lines:
                break
            continue
        if line.startswith("/"):
            break
        status_lines.append(line)
        if len(status_lines) >= 8:
            break
    return _one_line("\n".join(status_lines))


def _build_status_table(root: Path, snapshot: StatusSnapshot | None = None) -> Table:
    if snapshot is None:
        snapshot = _status_snapshot(root)
    config = snapshot.config
    rows = _workflow_rows(
        root,
        snapshot.active_items,
        snapshot.ready_items,
        config=config,
        live_workers=snapshot.live_records,
        canonical_records=snapshot.canonical_records,
    )

    table = Table(title="c3x workflow")
    table.add_column("State")
    table.add_column("Stage")
    table.add_column("Count", justify="right")
    table.add_column("Detail")
    for row in rows:
        table.add_row(row.state, row.stage, str(row.count), row.detail)
    table.add_row("capacity", "workers", str(config.limits.max_parallel_workers), "max parallel workers")
    return table


def _build_unstick_table(snapshot: StatusSnapshot) -> Table:
    candidates = _status_unstick_candidates(snapshot)
    table = Table(title="c3x unstick --dry-run")
    table.add_column("Task")
    table.add_column("Would")
    table.add_column("Reason")
    if not candidates:
        table.add_row("-", "no repair needed", "")
        return table
    for candidate in candidates[:10]:
        table.add_row(candidate.task_id, candidate.action, candidate.reason)
    return table


def _status_unstick_candidates(snapshot: StatusSnapshot) -> list[UnstickCandidate]:
    records = {record.task_id: record for record in snapshot.canonical_records}
    candidates: list[UnstickCandidate] = []
    for item in _with_labels(snapshot.active_items, {"flow"}):
        record = records.get(item.id)
        if record is None:
            if "running" in item.labels:
                candidates.append(
                    UnstickCandidate(
                        task_id=item.id,
                        action="mark-blocked-missing-run-record",
                        reason="Beads says running but no canonical run.json exists",
                        record_status="missing",
                        bead_status=item.status,
                    )
                )
            continue
        stale_running = "running" in item.labels and (
            record.status != "running" or record.pid is None or not _process_is_running(record.pid)
        )
        stale_terminal = item.status == "in_progress" and record.status in {"completed", "reviewed", "landed"}
        stale_review = "reviewing" in item.labels and record.status == "landed"
        if stale_running:
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="mark-blocked-stale-running",
                    reason="Beads says running but recorded worker is not live",
                    record_status=record.status,
                    bead_status=item.status,
                )
            )
        elif stale_review or (stale_terminal and record.status == "landed"):
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="close-landed",
                    reason="run record is landed but Beads still shows active state",
                    record_status=record.status,
                    bead_status=item.status,
                )
            )
        elif stale_terminal:
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="sync-terminal-state",
                    reason="run record is terminal but Beads still shows active state",
                    record_status=record.status,
                    bead_status=item.status,
                )
            )
    return candidates


def _workflow_rows(
    root: Path,
    open_items: list[BeadSummary],
    ready_items: list[BeadSummary],
    *,
    config: object | None = None,
    live_workers: list[RunRecord] | None = None,
    canonical_records: list[RunRecord] | None = None,
) -> list[WorkflowRow]:
    if config is None:
        config = load_config(root)
    if live_workers is None:
        live_workers = _live_worker_records(root)
    live_task_ids = {record.task_id for record in live_workers}
    if canonical_records is None:
        canonical_records = _canonical_run_records(root)
    records = {record.task_id: record for record in canonical_records}
    ready_ids = {item.id for item in ready_items if "flow" in item.labels}
    flow_items = [item for item in open_items if "flow" in item.labels]
    classified: dict[tuple[str, str], list[BeadSummary]] = {}
    for item in flow_items:
        key = _workflow_key(item, ready_ids=ready_ids, live_task_ids=live_task_ids, records=records)
        classified.setdefault(key, []).append(item)

    worker_slots = max(config.limits.max_parallel_workers - len(live_workers), 0)
    queued_count = len(classified.get(("not picked up", "queued"), []))
    queued_detail = "planned and waiting for supervisor"
    if queued_count and worker_slots:
        queued_detail = f"worker slots available: {worker_slots}; supervisor should dispatch"
    elif queued_count:
        queued_detail = "waiting for worker capacity"
    rows = [
        _workflow_row(classified, "not picked up", "submitted", "waiting for supervisor triage"),
        _workflow_row(classified, "not picked up", "questions", "waiting for human answer"),
        _workflow_row(
            classified,
            "not picked up",
            "queued",
            queued_detail,
        ),
        _workflow_row(classified, "being worked", "worker", "live worker process"),
        _workflow_row(classified, "being worked", "review", "worker done; review pending"),
        _workflow_row(classified, "being worked", "land", "review passed; merge pending"),
        _workflow_row(classified, "blocked", "blocked", "needs retry, unstick, or human action"),
        _workflow_row(classified, "unknown", "needs sync", "active Beads item does not match c3x labels"),
    ]
    rows.append(WorkflowRow("total", "open c3x items", len(flow_items), "sum of states above"))
    return rows


def _workflow_key(
    item: BeadSummary,
    *,
    ready_ids: set[str],
    live_task_ids: set[str],
    records: dict[str, RunRecord],
) -> tuple[str, str]:
    labels = set(item.labels)
    record = records.get(item.id)
    if "blocked" in labels or item.status == "blocked" or (record is not None and record.status == "blocked"):
        return ("blocked", "blocked")
    if "question" in labels or "needs-human-clarification" in labels:
        return ("not picked up", "questions")
    if "inbox" in labels or "idea" in labels or "unreviewed" in labels:
        return ("not picked up", "submitted")
    if item.id in live_task_ids:
        return ("being worked", "worker")
    if "reviewed" in labels or (record is not None and record.status == "reviewed"):
        return ("being worked", "land")
    if "reviewing" in labels or "completed-by-agent" in labels or (record is not None and record.status == "completed"):
        return ("being worked", "review")
    if "running" in labels:
        return ("blocked", "blocked")
    if item.id in ready_ids or "ready" in labels:
        return ("not picked up", "queued")
    return ("unknown", "needs sync")


def _workflow_row(
    classified: dict[tuple[str, str], list[BeadSummary]],
    state: str,
    stage: str,
    detail: str,
) -> WorkflowRow:
    items = classified.get((state, stage), [])
    ids = ", ".join(item.id for item in items[:4])
    extra = "" if len(items) <= 4 else f" +{len(items) - 4}"
    suffix = f": {ids}{extra}" if ids else ""
    return WorkflowRow(state, stage, len(items), f"{detail}{suffix}")


def _reviewing_items(items: list[BeadSummary]) -> list[BeadSummary]:
    return [
        item
        for item in _with_labels(items, {"flow", "reviewing"})
        if not {"reviewed", "landed", "blocked", "land-blocked"}.intersection(item.labels)
    ]


def _ready_to_land_items(items: list[BeadSummary]) -> list[BeadSummary]:
    return [
        item
        for item in _with_labels(items, {"flow", "reviewing", "reviewed"})
        if not {"landed", "blocked", "land-blocked"}.intersection(item.labels)
    ]


def _write_activity(root: Path, supervisor: str) -> None:
    _write_activity_event(root, supervisor, "")


def _write_activity_event(root: Path, event: str, detail: str = "") -> None:
    path = activity_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    updated_at = _now()
    events = _activity_events(_read_activity(root))
    events.insert(0, {"event": event, "detail": detail, "updated_at": updated_at})
    events = events[:10]
    path.write_text(
        json.dumps(
            {
                "supervisor": event if not detail else f"{event}; {detail}",
                "updated_at": updated_at,
                "events": events,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_activity(root: Path) -> dict[str, object]:
    path = activity_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"supervisor": "activity state is unreadable", "updated_at": ""}
    if not isinstance(data, dict):
        return {}
    return data


def _activity_events(activity: dict[str, object]) -> list[dict[str, str]]:
    raw_events = activity.get("events")
    events: list[dict[str, str]] = []
    if isinstance(raw_events, list):
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue
            event = raw_event.get("event")
            updated_at = raw_event.get("updated_at")
            detail = raw_event.get("detail", "")
            if isinstance(event, str) and isinstance(updated_at, str) and isinstance(detail, str):
                events.append({"event": event, "detail": detail, "updated_at": updated_at})
    if events:
        return events
    supervisor = activity.get("supervisor")
    updated_at = activity.get("updated_at")
    if isinstance(supervisor, str) and isinstance(updated_at, str):
        return [{"event": supervisor, "detail": "", "updated_at": updated_at}]
    return []


def _age_label(timestamp: str) -> str:
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    seconds = max(int((datetime.now(timezone.utc) - then).total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _supervisor_tick(
    root: Path,
    *,
    dispatch: bool,
    review: bool = False,
    land: bool = False,
    cleanup_done: bool = False,
    resolve_conflicts: bool = False,
) -> None:
    beads = _beads(root)
    if dispatch:
        _write_activity(root, "recovering interrupted workers")
        _recover_interrupted_workers(root, beads)
    _write_activity(root, "importing finished worker results")
    _import_finished_results(root, beads)
    _write_activity(root, "planning inbox items")
    _plan_inbox(root, beads)
    _write_activity(root, "checking critic tasks")
    critic_activity = _critic_tick(beads)
    _write_activity_event(root, "checking critic tasks", critic_activity)
    if dispatch:
        _write_activity(root, "checking worker capacity")
        config = load_config(root)
        running = len(_live_worker_records(root))
        slots = max(config.limits.max_parallel_workers - running, 0)
        dispatched = 0
        for task in beads.ready():
            if dispatched >= slots:
                break
            if "flow" in task.labels:
                _write_activity(root, f"dispatching worker {task.id}")
                _start_ready_worker(root, config, beads, task)
                beads.set_status(task.id, "in_progress")
                beads.add_labels(task.id, ["flow", "running"])
                beads.remove_labels(task.id, ["ready", "blocked", "reviewing"])
                dispatched += 1
        if dispatched == 0:
            _write_activity_event(root, "not dispatching", _supervisor_idle_reason(root, beads, dispatch=True))
    if review:
        _write_activity(root, "reviewing completed work")
        _auto_review(root, beads)
    if land:
        _write_activity(root, "landing reviewed work")
        _auto_land(root, beads, cleanup_done=cleanup_done)
    if resolve_conflicts:
        _write_activity(root, "checking merge-conflict blockers")
        config = load_config(root)
        resolved = 0
        for item_id in _conflict_task_ids(beads, task_id=None, all_tasks=True):
            _write_activity(root, f"starting conflict resolver {item_id}")
            _resolve_conflict_task(root, config, beads, item_id)
            resolved += 1
            console.print(f"[green]Conflict resolver started[/green] {item_id}")
        if resolved == 0:
            _write_activity_event(root, "not resolving conflicts", "no merge-conflict blockers waiting")
    _maybe_warn_stuck(root, beads)
    _write_activity_event(root, "tick complete", _supervisor_idle_reason(root, beads, dispatch=dispatch))


def _supervisor_idle_reason(root: Path, beads: Beads, *, dispatch: bool) -> str:
    if not dispatch:
        return "dispatch disabled; use c3x run --dispatch or c3x watch to start workers"
    config = load_config(root)
    active = _with_labels(beads.list_active(), {"flow"})
    live_workers = _live_worker_records(root)
    if len(live_workers) >= config.limits.max_parallel_workers:
        return f"worker capacity full: {len(live_workers)}/{config.limits.max_parallel_workers}"
    unstick_candidates = _unstick_candidates(root, beads, task_id=None, verify_mode="none")
    if unstick_candidates:
        first = unstick_candidates[0]
        return f"stale state detected; run c3x unstick --dry-run ({first.task_id}: {first.action})"
    flow_ready = [item for item in beads.ready() if "flow" in item.labels]
    if flow_ready:
        slots = max(config.limits.max_parallel_workers - len(live_workers), 0)
        return f"{len(flow_ready)} queued task(s), {slots} worker slot(s)"
    blocked = _with_labels(active, {"blocked"})
    questions = _with_labels(active, {"question", "needs-human-clarification"})
    inbox = _with_labels(active, {"inbox", "idea"})
    reviewing = _reviewing_items(active)
    landing = _ready_to_land_items(active)
    if active and len(blocked) == len(active):
        return f"all {len(blocked)} flow item(s) blocked"
    if questions:
        return f"{len(questions)} question(s) need human answers"
    if inbox:
        return f"{len(inbox)} inbox item(s) awaiting supervisor planning"
    if reviewing:
        return f"{len(reviewing)} item(s) awaiting review"
    if landing:
        return f"{len(landing)} item(s) awaiting land"
    if blocked:
        return f"{len(blocked)} blocked item(s); no queued worker task"
    return "no queued work"


def _plan_inbox(root: Path, beads: Beads) -> None:
    inbox_items = _with_labels(beads.list_open(), {"flow", "inbox", "idea"})
    for item in inbox_items:
        if "planned" in item.labels:
            continue
        if _questions_for_item(beads, item.id):
            continue
        if _needs_clarification(item):
            _create_clarification_question(beads, item)
            continue
        description = item.description or item.title
        created = beads.create_task(
            f"Implement: {item.title}",
            description=(
                f"Created by c3x architect from inbox item {item.id}.\n\n"
                f"Original feedback:\n{description}\n\n"
                "Acceptance criteria:\n- Implement the requested behavior.\n"
                "- Add or update verification when practical.\n"
            ),
            labels=["flow", "ready"],
            issue_type="task",
            priority=item.priority or 2,
        )
        child_id = str(created.get("id", "new task"))
        beads.add_note(item.id, f"Planned as {child_id}")
        beads.add_labels(item.id, ["planned"])
        beads.remove_labels(item.id, ["unreviewed"])
        beads.close(item.id, f"Planned as {child_id}")
        console.print(f"[green]Planned[/green] {item.id} -> {child_id}")


def _start_ready_worker(root: Path, config: object, beads: Beads, task: BeadSummary) -> RunRecord:
    source = _review_fix_source_record(root, beads, task)
    if source is None:
        return start_worker(root, config, task)
    return continue_worktree_worker(
        root,
        config,
        task,
        source,
        reason=f"repair review issue blocking {_review_fix_parent_id(beads, task) or 'the reviewed task'}",
    )


def _validate_item_interactively(root: Path, beads: Beads, item_id: str) -> None:
    while True:
        _plan_inbox(root, beads)
        item_questions = _questions_for_item(beads, item_id)
        if not item_questions:
            return
        _clarify_questions(beads, questions=item_questions)


def _clarify_questions(beads: Beads, questions: list[BeadSummary] | None = None) -> None:
    pending = questions if questions is not None else _open_questions(beads)
    if not pending:
        console.print("[green]No outstanding clarification questions.[/green]")
        return
    for question in pending:
        console.print(f"[bold]Question {question.id}[/bold]: {question.title}")
        if question.description:
            console.print(question.description)
        answer_text = typer.prompt("Answer")
        beads.add_note(question.id, f"Human answer: {answer_text}")
        beads.add_labels(question.id, ["answered"])
        beads.remove_labels(question.id, ["question", "needs-human-clarification"])
        blocked_item = _blocked_item_id(question)
        if blocked_item:
            beads.add_note(blocked_item, f"Clarification from {question.id}: {answer_text}")
            beads.add_labels(blocked_item, ["clarified"])
        beads.close(question.id, "Answered human clarification")


def _open_questions(beads: Beads) -> list[BeadSummary]:
    return _with_labels(beads.list_active(), {"flow", "question", "needs-human-clarification"})


def _questions_for_item(beads: Beads, item_id: str) -> list[BeadSummary]:
    return [
        question
        for question in _open_questions(beads)
        if question.description and f"Blocks: {item_id}" in question.description
    ]


def _needs_clarification(item: BeadSummary) -> bool:
    if "clarified" in item.labels:
        return False
    text = " ".join(part for part in (item.title, item.description or "") if part).strip()
    return len(text.split()) < 4


def _blocked_item_id(question: BeadSummary) -> str | None:
    if not question.description:
        return None
    for line in question.description.splitlines():
        if line.startswith("Blocks: "):
            return line.removeprefix("Blocks: ").strip() or None
    return None


def _create_clarification_question(beads: Beads, item: BeadSummary) -> None:
    existing = _questions_for_item(beads, item.id)
    if existing:
        return
    question = beads.create_task(
        f"Clarify: {item.title}",
        description=(
            f"Blocks: {item.id}\n\n"
            "The feedback is too underspecified to turn into safe worker tasks. "
            "Describe the desired behavior, affected area, and how you would recognize success."
        ),
        labels=["flow", "question", "needs-human-clarification"],
        issue_type="task",
        priority=item.priority or 2,
    )
    question_id = str(question.get("id", "new question"))
    beads.add_note(item.id, f"Needs human clarification: {question_id}")
    console.print(f"[yellow]Question[/yellow] {question_id} blocks {item.id}")


def _retry_task_ids(beads: Beads, *, task_id: str | None, all_tasks: bool) -> list[str]:
    if all_tasks and task_id:
        raise ValueError("pass either a task id or --all, not both")
    if not all_tasks and not task_id:
        raise ValueError("pass a task id or --all")
    if task_id:
        return [task_id]
    blocked = _with_labels(beads.list_active(), {"flow", "blocked"})
    return [item.id for item in blocked]


def _retry_mode_from_flags(
    *,
    resume_session: bool,
    continue_worktree: bool,
    fresh: bool,
) -> RetryMode:
    selected = [
        mode
        for mode, enabled in (
            ("session", resume_session),
            ("worktree", continue_worktree),
            ("fresh", fresh),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise ValueError("pass only one retry mode: --resume-session, --continue-worktree, or --fresh")
    return selected[0] if selected else "session"


def _conflict_task_ids(beads: Beads, *, task_id: str | None, all_tasks: bool) -> list[str]:
    if all_tasks and task_id:
        raise ValueError("pass either a task id or --all, not both")
    if not all_tasks and not task_id:
        raise ValueError("pass a task id or --all")
    if task_id:
        task = beads.show(task_id)
        if not {"flow", "land-blocked", "blocker-merge-conflict"}.issubset(set(task.labels)):
            raise ValueError(f"{task_id} is not blocked on a merge conflict")
        return [task_id]
    blocked = _with_labels(beads.list_active(), {"flow", "land-blocked", "blocker-merge-conflict"})
    return [item.id for item in blocked if "running" not in item.labels]


def _retry_task(
    root: Path,
    config: object,
    beads: Beads,
    task_id: str,
    *,
    retry_mode: RetryMode = "session",
) -> tuple[RunRecord, RetryMode]:
    task = beads.show(task_id)
    _ensure_retryable(root, task_id)
    _clear_review_cleanup_blockers(beads, task)
    previous = _review_fix_source_record(root, beads, task) or _current_run_record(root, task_id)
    session_id = _session_id_for_run(previous) if previous is not None else None
    attempt = _next_attempt(root, task_id)
    _archive_current_run(root, task_id, archive_attempt=attempt)
    beads.set_status(task_id, "open")
    beads.add_labels(task_id, ["flow", "ready"])
    beads.remove_labels(task_id, _retry_removed_labels(task))
    worktree_exists = previous is not None and Path(previous.worktree).exists()
    reason = _retry_reason(task, previous) if previous is not None else "retry requested"
    if _is_review_fix(task) and worktree_exists:
        record = continue_worktree_worker(root, config, task, previous, reason=reason, attempt=attempt)
        mode_used = "worktree"
        note = f"c3x continued source review worktree as attempt {record.attempt} from {previous.task_id}"
    elif retry_mode == "session" and worktree_exists and session_id:
        record = resume_session_worker(
            root,
            config,
            task,
            previous,
            session_id=session_id,
            reason=reason,
            attempt=attempt,
        )
        mode_used: RetryMode = "session"
        note = f"c3x resumed session {session_id} as attempt {record.attempt}"
    elif retry_mode in {"session", "worktree"} and worktree_exists:
        record = continue_worktree_worker(root, config, task, previous, reason=reason, attempt=attempt)
        mode_used = "worktree"
        note = f"c3x continued worktree as attempt {record.attempt} from attempt {previous.attempt}"
    else:
        record = start_worker(root, config, task, attempt=attempt)
        mode_used = "fresh"
        note = f"c3x retry started attempt {record.attempt}"
    beads.set_status(task_id, "in_progress")
    beads.add_labels(task_id, ["flow", "running", f"attempt-{record.attempt}"])
    beads.remove_labels(task_id, ["ready", "reviewing", "blocked"])
    beads.add_note(task_id, note)
    return record, mode_used


def _clear_review_cleanup_blockers(beads: Beads, task: BeadSummary) -> None:
    cleanup_edges = _review_cleanup_task_edges(beads, task.id)
    if not cleanup_edges:
        return
    for cleanup, blocked_id in reversed(cleanup_edges):
        try:
            beads.remove_blocker(cleanup.id, blocked_id)
        except BeadsError:
            raise
        if cleanup.status != "closed":
            beads.close(cleanup.id, f"Superseded by retry of {task.id}")
    beads.add_note(task.id, f"c3x retry cleared {len(cleanup_edges)} superseded review cleanup task(s)")


def _is_review_fix(task: BeadSummary) -> bool:
    return "review-fix" in task.labels


def _review_fix_source_record(root: Path, beads: Beads, task: BeadSummary) -> RunRecord | None:
    if not _is_review_fix(task):
        return None
    ancestor_id = _review_fix_ancestor_id(beads, task)
    if not ancestor_id:
        return None
    record = _current_run_record(root, ancestor_id)
    if record is None:
        return None
    if not Path(record.worktree).exists():
        return None
    return record


def _review_fix_ancestor_id(beads: Beads, task: BeadSummary) -> str | None:
    seen = {task.id}
    current = task
    while True:
        parent_id = _review_fix_parent_id(beads, current)
        if not parent_id or parent_id in seen:
            return None
        seen.add(parent_id)
        try:
            parent = beads.show(parent_id)
        except BeadsError:
            return parent_id
        if not _is_review_fix(parent):
            return parent_id
        current = parent


def _review_fix_parent_id(beads: Beads, task: BeadSummary) -> str | None:
    parent_id = _blocked_item_id(task)
    if parent_id:
        return parent_id
    try:
        dependencies = beads.dependencies(task.id, direction="up", dep_type="blocks")
    except BeadsError:
        return None
    for dependency in dependencies:
        blocked_id = _dependency_blocked_id(dependency, task.id)
        if blocked_id:
            return blocked_id
    return None


def _review_cleanup_tasks(beads: Beads, task_id: str) -> list[BeadSummary]:
    return [cleanup for cleanup, _ in _review_cleanup_task_edges(beads, task_id)]


def _review_cleanup_task_edges(
    beads: Beads,
    task_id: str,
    *,
    seen: set[str] | None = None,
) -> list[tuple[BeadSummary, str]]:
    seen = seen or {task_id}
    cleanup_edges: list[tuple[BeadSummary, str]] = []
    for cleanup in _direct_review_cleanup_tasks(beads, task_id):
        if cleanup.id in seen:
            continue
        seen.add(cleanup.id)
        cleanup_edges.append((cleanup, task_id))
        cleanup_edges.extend(_review_cleanup_task_edges(beads, cleanup.id, seen=seen))
    return cleanup_edges


def _direct_review_cleanup_tasks(beads: Beads, task_id: str) -> list[BeadSummary]:
    cleanup_by_id: dict[str, BeadSummary] = {}
    for item in beads.list_active():
        if "review-fix" in item.labels and _blocked_item_id(item) == task_id:
            cleanup_by_id[item.id] = item
    for dependency in beads.dependencies(task_id, direction="down", dep_type="blocks"):
        blocker_id = _dependency_blocker_id(dependency, task_id)
        if not blocker_id:
            continue
        try:
            blocker = beads.show(blocker_id)
        except BeadsError:
            continue
        if "review-fix" in blocker.labels or _blocked_item_id(blocker) == task_id:
            cleanup_by_id[blocker.id] = blocker
    return list(cleanup_by_id.values())


def _dependency_blocker_id(dependency: dict[str, object], blocked_id: str) -> str | None:
    for key in ("depends_on_id", "dependency_id", "blocked_by", "blocker_id", "to_id", "target_id"):
        value = dependency.get(key)
        if isinstance(value, str) and value and value != blocked_id:
            return value
    for key in ("id", "issue_id", "from_id", "source_id"):
        value = dependency.get(key)
        if isinstance(value, str) and value and value != blocked_id:
            return value
    return None


def _dependency_blocked_id(dependency: dict[str, object], blocker_id: str) -> str | None:
    for key in ("blocked_id", "issue_id", "to_id", "target_id", "dependent_id", "id"):
        value = dependency.get(key)
        if isinstance(value, str) and value and value != blocker_id:
            return value
    return None


def _review_cleanup_verification_issues(beads: Beads, task_id: str) -> tuple[str, ...]:
    cleanup_tasks = _review_cleanup_tasks(beads, task_id)
    if not cleanup_tasks:
        return ()
    task_list = ", ".join(task.id for task in cleanup_tasks)
    return (f"open review cleanup blockers must be fixed first: {task_list}",)


def _review_cleanup_index(items: list[BeadSummary]) -> dict[str, list[BeadSummary]]:
    cleanup_by_blocked: dict[str, list[BeadSummary]] = {}
    for item in items:
        if "review-fix" not in item.labels:
            continue
        blocked_id = _blocked_item_id(item)
        if blocked_id:
            cleanup_by_blocked.setdefault(blocked_id, []).append(item)
    return cleanup_by_blocked


def _review_cleanup_verification_issues_from_index(
    cleanup_by_blocked: dict[str, list[BeadSummary]],
    task_id: str,
) -> tuple[str, ...]:
    cleanup_tasks = _review_cleanup_tasks_from_index(cleanup_by_blocked, task_id)
    if not cleanup_tasks:
        return ()
    task_list = ", ".join(task.id for task in cleanup_tasks)
    return (f"open review cleanup blockers must be fixed first: {task_list}",)


def _review_cleanup_tasks_from_index(
    cleanup_by_blocked: dict[str, list[BeadSummary]],
    task_id: str,
    *,
    seen: set[str] | None = None,
) -> list[BeadSummary]:
    seen = seen or {task_id}
    cleanup_tasks: list[BeadSummary] = []
    for cleanup in cleanup_by_blocked.get(task_id, []):
        if cleanup.id in seen:
            continue
        seen.add(cleanup.id)
        cleanup_tasks.append(cleanup)
        cleanup_tasks.extend(_review_cleanup_tasks_from_index(cleanup_by_blocked, cleanup.id, seen=seen))
    return cleanup_tasks


def _resolve_conflict_task(root: Path, config: object, beads: Beads, task_id: str) -> RunRecord:
    task = beads.show(task_id)
    _ensure_retryable(root, task_id)
    previous = _load_repaired_current_run_record(root, task_id)
    original_result = _read_original_result(root, task_id)
    target_branch = current_branch(root)
    target_revision = rev_parse(root, "HEAD")
    attempt = _next_attempt(root, task_id)
    _archive_current_run(root, task_id, archive_attempt=attempt)
    beads.set_status(task_id, "open")
    beads.add_labels(task_id, ["flow", "ready"])
    beads.remove_labels(task_id, _retry_removed_labels(task))
    record = start_conflict_resolver(
        root,
        config,
        task,
        source_branch=previous.branch,
        target_branch=target_branch,
        target_revision=target_revision,
        original_result=original_result,
        attempt=attempt,
    )
    _seed_conflict_resolver_result(root, task_id, original_result, attempt=record.attempt)
    beads.set_status(task_id, "in_progress")
    beads.add_labels(task_id, ["flow", "running", "conflict-resolver", f"attempt-{record.attempt}"])
    beads.remove_labels(task_id, ["ready", "reviewing", "blocked"])
    beads.add_note(task_id, f"c3x conflict resolver started attempt {record.attempt}")
    return record


def _seed_conflict_resolver_result(root: Path, task_id: str, original_result: str, *, attempt: int) -> None:
    try:
        WorkerResult.model_validate_json(original_result)
    except ValueError:
        original_result = WorkerResult(
            task_id=task_id,
            status="blocked",
            summary="Original worker result was unavailable when conflict resolution started.",
            task_kind="merge-conflict",
            attempt=attempt,
            blockers=["Missing or invalid original result.json before conflict resolver attempt."],
            blocker_category="result-missing",
            confidence="low",
            unfinished=[],
        ).model_dump_json(indent=2) + "\n"
    _save_canonical_result(root, task_id, original_result)


def _read_original_result(root: Path, task_id: str) -> str:
    path = result_path(root, task_id)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "{}"


def _ensure_retryable(root: Path, task_id: str) -> None:
    path = run_record_path(root, task_id)
    if not path.exists():
        return
    record = RunRecord.load(path)
    if record.status == "landed":
        raise ValueError(f"{task_id} is already landed")
    if record.status == "running" and record.pid is not None and _process_is_running(record.pid):
        raise ValueError(f"{task_id} still has a running worker pid {record.pid}")


def _current_run_record(root: Path, task_id: str) -> RunRecord | None:
    path = run_record_path(root, task_id)
    if not path.exists():
        return None
    return _load_repaired_current_run_record(root, task_id)


def _load_repaired_current_run_record(root: Path, task_id: str) -> RunRecord:
    path = run_record_path(root, task_id)
    record = RunRecord.load(path)
    repaired = _repaired_run_record(path, record)
    if _can_repair_from_completed_evidence(repaired) and _record_points_to_missing_result_or_worktree(repaired):
        completed_evidence = _completed_result_evidence(root, task_id)
        if completed_evidence is not None:
            evidence_record, _ = completed_evidence
            repaired = repaired.model_copy(
                update={
                    "branch": evidence_record.branch,
                    "worktree": evidence_record.worktree,
                    "result": evidence_record.result,
                    "attempt": evidence_record.attempt,
                }
            )
    if repaired != record:
        repaired.save(path)
    return repaired


def _record_points_to_missing_result_or_worktree(record: RunRecord) -> bool:
    return not Path(record.worktree).exists() or not Path(record.result).exists()


def _can_repair_from_completed_evidence(record: RunRecord) -> bool:
    return record.status in {"completed", "reviewed", "landed"}


def _session_id_for_run(record: RunRecord | None) -> str | None:
    if record is None:
        return None
    for path in (Path(record.prompt).parent / "stderr.log", Path(record.prompt).parent / "stdout.log"):
        session_id = _extract_session_id(_read_tail(path, max_chars=12000))
        if session_id:
            return session_id
    return None


def _extract_session_id(text: str) -> str | None:
    match = re.search(
        r"session id:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        text,
    )
    return match.group(1) if match else None


def _retry_reason(task: BeadSummary, previous: RunRecord) -> str:
    reason = _blocked_reason(task)
    if reason and reason != "blocked label present":
        return reason
    if previous.outcome:
        return previous.outcome
    return "retry requested"


def _is_transient_worker_failure(record: RunRecord) -> bool:
    if not Path(record.worktree).exists():
        return False
    evidence = "\n".join(
        [
            _read_tail(Path(record.prompt).parent / "stderr.log", max_chars=12000),
            _read_tail(Path(record.prompt).parent / "stdout.log", max_chars=12000),
            _read_tail(Path(record.last_message), max_chars=4000),
        ]
    ).lower()
    return _has_usage_limit_evidence(evidence) or any(
        pattern in evidence
        for pattern in (
            "rate limit",
            "429",
            "failed to connect",
            "failed to lookup address information",
            "stream disconnected before completion",
            "error sending request",
            "connection reset",
            "temporarily unavailable",
            "timeout",
            "timed out",
        )
    )


def _supervisor_retry_mode(record: RunRecord) -> RetryMode:
    """Supervisor retry charter: preserve the most context that can be safely reused."""
    if _session_id_for_run(record):
        return "session"
    if Path(record.worktree).exists():
        return "worktree"
    return "fresh"


def _archive_current_run(root: Path, task_id: str, *, archive_attempt: int | None = None) -> None:
    run_dir = run_record_path(root, task_id).parent
    if not run_dir.exists():
        return
    try:
        record = RunRecord.load(run_dir / "run.json")
        suffix = f"attempt-{archive_attempt or _record_attempt(record)}"
    except Exception:
        suffix = "previous"
    target = _unique_archive_path(run_dir.with_name(f"{task_id}-{suffix}"))
    run_dir.rename(target)
    _repair_archived_run_record_paths(target / "run.json")


def _unique_archive_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _repair_archived_run_record_paths(record_path: Path) -> None:
    if not record_path.exists():
        return
    record = RunRecord.load(record_path)
    repaired = _repaired_run_record(record_path, record)
    if repaired != record:
        repaired.save(record_path)


def _repaired_run_record(
    record_path: Path,
    record: RunRecord,
    *,
    branch_by_worktree: dict[Path, str] | None = None,
) -> RunRecord:
    run_dir = record_path.parent
    updates: dict[str, object] = {}
    for field_name in ("prompt", "last_message"):
        path = Path(getattr(record, field_name))
        archived_path = run_dir / path.name
        if path.parent != run_dir and archived_path.exists():
            updates[field_name] = str(archived_path)

    reported_result = _reported_result_path(run_dir / Path(updates.get("last_message", record.last_message)).name)
    if reported_result and reported_result.exists() and Path(record.result) != reported_result:
        updates["result"] = str(reported_result)
        worktree = _worktree_from_result_path(reported_result)
        if worktree is not None:
            updates["worktree"] = str(worktree)

    repaired_worktree = Path(str(updates.get("worktree", record.worktree)))
    repaired_branch = _branch_for_worktree(repaired_worktree, branch_by_worktree=branch_by_worktree)
    if repaired_branch and repaired_branch != record.branch:
        updates["branch"] = repaired_branch

    repaired_attempt = _record_attempt(record.model_copy(update=updates))
    if repaired_attempt != record.attempt:
        updates["attempt"] = repaired_attempt

    return record.model_copy(update=updates) if updates else record


def _branch_for_worktree(
    worktree: Path,
    *,
    branch_by_worktree: dict[Path, str] | None = None,
) -> str | None:
    if branch_by_worktree is not None:
        return branch_by_worktree.get(worktree)
    if not worktree.exists():
        return None
    try:
        return current_branch(worktree)
    except GitError:
        return None


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


def _reported_result_path(last_message_path: Path) -> Path | None:
    text = _read_tail(last_message_path, max_chars=12000)
    if not text:
        return None
    patterns = (
        r"Result written to \[`\.c3x/result\.json`\]\(([^)]+)\)",
        r"Result written to \[`\.c3x/[^`\]]+-result\.json`\]\(([^)]+)\)",
        r"Wrote \[`\.c3x/result\.json`\]\(([^)]+)\)",
        r"Wrote \[`\.c3x/[^`\]]+-result\.json`\]\(([^)]+)\)",
        r"session result saved at\s+(\S+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return Path(match.group(1)).expanduser()
    return None


def _worktree_from_result_path(path: Path) -> Path | None:
    if (path.name == "result.json" or path.name.endswith("-result.json")) and path.parent.name == ".c3x":
        return path.parent.parent
    return None


def _retry_removed_labels(task: BeadSummary) -> list[str]:
    labels = {
        "blocked",
        "running",
        "reviewing",
        "reviewed",
        "completed-by-agent",
        "conflict-resolver",
        "rejected",
        "review-blocked",
        "land-blocked",
    }
    labels.update(label for label in task.labels if label.startswith("blocker-"))
    return sorted(labels)


def _squash_plans(root: Path, *, task_id: str | None, all_tasks: bool) -> list[SquashPlan]:
    if all_tasks and task_id:
        raise ValueError("pass either a task id or --all, not both")
    if not all_tasks and not task_id:
        raise ValueError("pass a task id or --all")
    records = [record for _, record in _run_record_paths(root) if record.status == "landed"]
    if task_id:
        record = next((record for record in records if record.task_id == task_id), None)
        if record is None:
            raise ValueError(f"{task_id} is not a landed c3x task")
        plan = _squash_plan_for_head(root, record, "HEAD")
        if plan is None:
            raise ValueError(f"{task_id} has no eligible squash segment at HEAD")
        return [plan]

    plan = _first_squash_plan_for_head(root, records, "HEAD")
    return [] if plan is None else [plan]


def _first_squash_plan_for_head(root: Path, records: list[RunRecord], rev: str) -> SquashPlan | None:
    for record in records:
        plan = _squash_plan_for_head(root, record, rev)
        if plan is not None:
            return plan
    return None


def _squash_plan_for_head(root: Path, record: RunRecord, rev: str) -> SquashPlan | None:
    current = rev_parse(root, rev)
    commits: list[str] = []
    subjects: list[str] = []
    while True:
        subject = commit_subject(root, current)
        if not _subject_belongs_to_task(record, subject):
            break
        commits.append(current)
        subjects.append(subject)
        parents = commit_parents(root, current)
        if not parents:
            break
        current = parents[0]
    if len(commits) < 2:
        return None
    if not any(record.task_id in subject or record.branch in subject for subject in subjects):
        return None
    return SquashPlan(
        task_id=record.task_id,
        base=current,
        commits=tuple(commits),
        message=_squash_message(root, record.task_id),
    )


def _subject_belongs_to_task(record: RunRecord, subject: str) -> bool:
    if record.task_id in subject or record.branch in subject:
        return True
    return subject == "Checkpoint c3x ledger before merge"


def _squash_message(root: Path, task_id: str) -> str:
    path = result_path(root, task_id)
    if path.exists():
        try:
            result = WorkerResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            result = None
        if result and result.summary:
            return f"Complete c3x task {task_id}\n\n{result.summary}"
    return f"Complete c3x task {task_id}"


def _cleanup_actions(root: Path, *, task_id: str | None, require_task_cleanup: bool = True) -> list[CleanupAction]:
    records = _run_record_paths(root)
    try:
        branch_by_worktree = worktree_branches(root)
    except GitError:
        branch_by_worktree = None
    canonical = {
        record.task_id: record
        for path, record in records
        if path == run_record_path(root, record.task_id)
    }
    actions: list[CleanupAction] = []
    for path, record in records:
        if task_id and record.task_id != task_id:
            continue
        run_dir = path.parent
        if _run_record_needs_repair(path, record, branch_by_worktree=branch_by_worktree):
            actions.append(
                CleanupAction(
                    task_id=record.task_id,
                    run_dir=run_dir,
                    worktree=Path(record.worktree),
                    branch=record.branch,
                    reason=(
                        "current run metadata"
                        if path == run_record_path(root, record.task_id)
                        else "archived run metadata"
                    ),
                    repair_run_record=True,
                )
            )
            continue
        if path == run_record_path(root, record.task_id):
            if record.status == "landed":
                try:
                    merged = _landed_record_has_merge_evidence(root, record)
                except GitError as exc:
                    if not _is_missing_ref_error(exc):
                        raise
                    if not Path(record.worktree).exists():
                        continue
                    actions.append(
                        CleanupAction(
                            task_id=record.task_id,
                            run_dir=run_dir,
                            worktree=Path(record.worktree),
                            branch=record.branch,
                            reason="landed worktree with missing branch",
                        )
                    )
                    continue
                actions.append(
                    CleanupAction(
                        task_id=record.task_id,
                        run_dir=run_dir,
                        worktree=Path(record.worktree),
                        branch=record.branch,
                        reason="landed worktree" if merged else "landed but unmerged branch",
                        repair_merge=not merged,
                    )
                )
            continue
        current = canonical.get(record.task_id)
        if _is_superseded_attempt(record, current):
            actions.append(
                CleanupAction(
                    task_id=record.task_id,
                    run_dir=run_dir,
                    worktree=Path(record.worktree),
                    branch=record.branch,
                    reason=f"superseded attempt {record.attempt}",
                    remove_run_dir=True,
                )
            )
    if task_id and not actions and require_task_cleanup:
        current = canonical.get(task_id)
        if current and current.status != "landed":
            raise ValueError(f"{task_id} is not landed and has no superseded attempts")
    return actions


def _landed_record_has_merge_evidence(root: Path, record: RunRecord) -> bool:
    if record.outcome == "review-resolved":
        return True
    if record.landed_revision and record.landing_branch:
        try:
            if is_ancestor(root, record.landed_revision, record.landing_branch):
                return True
        except GitError as exc:
            if not _is_missing_ref_error(exc):
                raise
    if is_ancestor(root, record.branch, "HEAD"):
        return True
    return history_has_subject(root, "HEAD", f"Complete c3x task {record.task_id}") or history_has_subject(
        root,
        "HEAD",
        f"Merge {record.branch}",
    )


def _run_record_needs_repair(
    path: Path,
    record: RunRecord,
    *,
    branch_by_worktree: dict[Path, str] | None = None,
) -> bool:
    return _repaired_run_record(path, record, branch_by_worktree=branch_by_worktree) != record


def _repair_large_beads_payloads(root: Path, beads: Beads, *, task_id: str | None, dry_run: bool) -> None:
    del root
    items = [beads.show(task_id)] if task_id else _with_labels(beads.list_active(), {"flow"})
    candidates = [item for item in items if _bead_payload_size(item) >= 12_000]
    if not candidates:
        console.print("[green]No oversized Beads payloads found.[/green]")
        return
    for item in candidates:
        before_size = _bead_payload_size(item)
        summary = _large_bead_compaction_summary(item, before_size)
        after_size = _compacted_bead_payload_size(summary)
        if dry_run:
            console.print(
                f"[yellow]Would repair Beads payload[/yellow] {item.id}: "
                f"{_format_bytes(before_size)} -> {_format_bytes(after_size)}"
            )
            continue
        beads.compact_issue(item.id, summary, issue=item)
        console.print(
            f"[green]Repaired Beads payload[/green] {item.id}: "
            f"{_format_bytes(before_size)} -> {_format_bytes(after_size)}"
        )


def _bead_payload_size(item: BeadSummary) -> int:
    return len((item.description or "").encode("utf-8")) + len((item.notes or "").encode("utf-8"))


def _compacted_bead_payload_size(summary: str) -> int:
    notes = "c3x compacted oversized notes into the issue description summary."
    return len(summary.encode("utf-8")) + len(notes.encode("utf-8"))


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _large_bead_compaction_summary(item: BeadSummary, size: int) -> str:
    labels = ", ".join(item.labels) if item.labels else "none"
    body = item.description or item.notes or ""
    return (
        "c3x compacted this Beads entry because its description/notes payload "
        "was too large for Beads event-log updates.\n\n"
        f"Original payload size: {size} bytes\n"
        f"Title: {item.title}\n"
        f"Status: {item.status or 'unknown'}\n"
        f"Labels: {labels}\n\n"
        "Preserved excerpt:\n"
        f"{_compact_excerpt(body, limit=1200)}\n\n"
        "Run `bd restore "
        f"{item.id}` to inspect pre-compaction content if Dolt history still contains it."
    )


def _compact_excerpt(text: str, *, limit: int) -> str:
    normalized = _one_line(text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _cleanup_reconcile_labels(root: Path, beads: Beads, *, task_id: str | None, dry_run: bool) -> int:
    items = _cleanup_reconcile_items(root, beads, task_id=task_id)
    changes = 0
    for item in items:
        labels = set(item.labels)
        record = _current_run_record(root, item.id)
        if record is not None and record.status == "landed" and "landed" in labels:
            stale = _stale_labels_for_landed_task(labels)
            if stale:
                message = f"{item.id}: remove stale labels {', '.join(stale)}"
                if dry_run:
                    console.print(f"[yellow]Would reconcile labels[/yellow] {message}")
                else:
                    beads.remove_labels(item.id, stale)
                    console.print(f"[green]Reconciled labels[/green] {message}")
                changes += 1
            continue
        if record is not None and record.status == "running" and "running" in labels:
            stale = _stale_labels_for_running_task(labels)
            if stale:
                message = f"{item.id}: remove stale labels {', '.join(stale)}"
                if dry_run:
                    console.print(f"[yellow]Would reconcile labels[/yellow] {message}")
                else:
                    beads.remove_labels(item.id, stale)
                    console.print(f"[green]Reconciled labels[/green] {message}")
                changes += 1
            continue

        groups = _label_state_groups(labels)
        if len(groups) > 1:
            console.print(
                f"[yellow]Label conflict[/yellow] {item.id}: {', '.join(groups)}. "
                f"Recommended: c3x retry {item.id} --fresh"
            )
            changes += 1
    return changes


def _cleanup_reconcile_items(root: Path, beads: Beads, *, task_id: str | None) -> list[BeadSummary]:
    if task_id:
        return [beads.show(task_id)]
    by_id = {item.id: item for item in _with_labels(beads.list_active(), {"flow"})}
    for _, record in _canonical_run_record_paths(root):
        if record.status != "landed" or record.task_id in by_id:
            continue
        try:
            item = beads.show(record.task_id)
        except BeadsError:
            continue
        if "flow" in item.labels:
            by_id[item.id] = item
    return list(by_id.values())


def _stale_labels_for_running_task(labels: set[str]) -> list[str]:
    stale = {
        "blocked",
        "reviewing",
        "reviewed",
        "completed-by-agent",
        "conflict-resolver",
        "rejected",
        "review-blocked",
        "land-blocked",
        "landed",
    }
    stale.update(label for label in labels if label.startswith("blocker-"))
    return sorted(stale.intersection(labels))


def _stale_labels_for_landed_task(labels: set[str]) -> list[str]:
    stale = {
        "blocked",
        "ready",
        "running",
        "reviewing",
        "reviewed",
        "completed-by-agent",
        "conflict-resolver",
        "rejected",
        "review-blocked",
        "land-blocked",
    }
    stale.update(label for label in labels if label.startswith("blocker-"))
    return sorted(stale.intersection(labels))


def _label_state_groups(labels: set[str]) -> list[str]:
    groups: list[str] = []
    if "ready" in labels:
        groups.append("ready")
    if "running" in labels:
        groups.append("running")
    if {"reviewing", "reviewed", "completed-by-agent"}.intersection(labels):
        groups.append("review")
    if {"blocked", "land-blocked", "review-blocked", "rejected"}.intersection(labels) or any(
        label.startswith("blocker-") for label in labels
    ):
        groups.append("blocked")
    if "landed" in labels:
        groups.append("landed")
    return groups


def _is_superseded_attempt(record: RunRecord, current: RunRecord | None) -> bool:
    if current is None:
        return False
    if current.attempt <= record.attempt:
        return False
    return current.status in {"completed", "reviewed", "landed"}


def _is_missing_ref_error(exc: GitError) -> bool:
    message = str(exc).lower()
    return (
        "not a valid object name" in message
        or "unknown revision" in message
        or "ambiguous argument" in message
        or "not a valid ref" in message
    )


def _unstick_candidates(root: Path, beads: Beads, *, task_id: str | None, verify_mode: str) -> list[UnstickCandidate]:
    active_items = _with_labels(beads.list_active(), {"flow"})
    items = [beads.show(task_id)] if task_id else active_items
    cleanup_by_blocked = _review_cleanup_index(active_items)
    closed_cleanup_by_blocked: dict[str, list[BeadSummary]] = {}
    if any({"review-blocked", "blocker-review-issues"}.intersection(item.labels) for item in items):
        list_closed = getattr(beads, "list_closed", None)
        if callable(list_closed):
            closed_cleanup_by_blocked = _review_cleanup_index(list_closed())
    run_records = _run_record_paths(root)
    records_by_task = {
        record.task_id: record
        for path, record in run_records
        if path == run_record_path(root, record.task_id)
    }
    candidates: list[UnstickCandidate] = []
    conflict_scan: bool | None = None
    for item in items:
        record = records_by_task.get(item.id)
        if record is not None and {"review-blocked", "blocker-review-issues"}.intersection(item.labels):
            direct_cleanup_tasks = [
                *cleanup_by_blocked.get(item.id, []),
                *closed_cleanup_by_blocked.get(item.id, []),
            ]
            if not direct_cleanup_tasks:
                direct_cleanup_tasks = _direct_review_cleanup_tasks(beads, item.id)
            if direct_cleanup_tasks and all(cleanup.status == "closed" for cleanup in direct_cleanup_tasks):
                candidates.append(
                    UnstickCandidate(
                        task_id=item.id,
                        action="close-review-resolved",
                        reason="all direct review cleanup blockers are closed",
                        record_status=record.status,
                        bead_status=item.status,
                    )
                )
                continue

        completed_evidence = _completed_result_evidence(root, item.id, run_records=run_records)
        if completed_evidence is not None and _needs_completed_result_state_repair(item, completed_evidence[0]):
            record, result = completed_evidence
            review_cleanup_issues = _review_cleanup_verification_issues_from_index(cleanup_by_blocked, item.id)
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="mark-completed-from-result",
                    reason=f"completed result.json exists at {record.result} but Beads/run state is stale",
                    record_status=record.status,
                    bead_status=item.status,
                    verification_issues=review_cleanup_issues,
                    cheap_commands=tuple(command.command for command in result.verification),
                )
            )
            continue

        record = records_by_task.get(item.id)
        if record is None:
            if "running" in item.labels:
                candidates.append(
                    UnstickCandidate(
                        task_id=item.id,
                        action="mark-blocked-missing-run-record",
                        reason="Beads says running but no canonical run.json exists",
                        record_status="missing",
                        bead_status=item.status,
                    )
                )
            continue
        stale_running = "running" in item.labels and (
            record.status != "running" or record.pid is None or not _process_is_running(record.pid)
        )
        stale_terminal = item.status == "in_progress" and record.status in {"completed", "reviewed", "landed"}
        stale_review = "reviewing" in item.labels and record.status == "landed"
        if not (stale_running or stale_terminal or stale_review):
            continue

        review_cleanup_issues = _review_cleanup_verification_issues_from_index(cleanup_by_blocked, item.id)
        verification_issues: tuple[str, ...] = ()
        cheap_commands: tuple[str, ...] = ()
        if verify_mode in {"cheap", "recorded"} and record.status in {"completed", "reviewed", "landed"}:
            if conflict_scan is None:
                conflict_scan = _scan_conflict_markers(root)
            verification_issues, cheap_commands = _cheap_unstick_verification(
                root,
                record,
                conflict_scan=conflict_scan,
                run_commands=verify_mode == "cheap",
            )
        verification_issues = (*verification_issues, *review_cleanup_issues)

        if record.status == "landed":
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="close-landed",
                    reason="run record is landed but Beads still shows active state",
                    record_status=record.status,
                    bead_status=item.status,
                    verification_issues=verification_issues,
                    cheap_commands=cheap_commands,
                )
            )
            continue

        if record.status in {"completed", "reviewed"} and _branch_is_safely_contained(root, record):
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="close-contained",
                    reason="worker branch is already contained in HEAD but Beads still shows active state",
                    record_status=record.status,
                    bead_status=item.status,
                    verification_issues=verification_issues,
                    cheap_commands=cheap_commands,
                )
            )
            continue

        if record.status == "completed" and {"reviewed", "reviewing"}.intersection(item.labels):
            candidates.append(
                UnstickCandidate(
                    task_id=item.id,
                    action="mark-reviewed",
                    reason="Beads already has review labels but the run record is still completed",
                    record_status=record.status,
                    bead_status=item.status,
                    verification_issues=verification_issues,
                    cheap_commands=cheap_commands,
                )
            )
    return candidates


def _completed_result_evidence(
    root: Path,
    task_id: str,
    *,
    run_records: list[tuple[Path, RunRecord]] | None = None,
) -> tuple[RunRecord, WorkerResult] | None:
    if run_records is None:
        run_records = _run_record_paths(root)
    matches: list[tuple[float, RunRecord, WorkerResult]] = []
    for record_path, record in run_records:
        if record.task_id != task_id:
            continue
        result_path_for_record = _result_file_for_record(record)
        if not result_path_for_record.exists():
            continue
        try:
            result = WorkerResult.model_validate_json(result_path_for_record.read_text(encoding="utf-8"))
        except Exception:
            continue
        if result.task_id != task_id or result.status != "completed":
            continue
        repaired = record.model_copy(update={"result": str(result_path_for_record)})
        worktree = _worktree_from_result_path(result_path_for_record)
        if worktree is not None:
            repaired = repaired.model_copy(update={"worktree": str(worktree)})
            branch = _branch_for_worktree(worktree)
            if branch:
                repaired = repaired.model_copy(update={"branch": branch})
        repaired = repaired.model_copy(update={"attempt": _record_attempt(repaired)})
        matches.append((record_path.stat().st_mtime, repaired, result))
    if not matches:
        return None
    _, record, result = max(matches, key=lambda item: item[0])
    return record, result


def _needs_completed_result_state_repair(item: BeadSummary, record: RunRecord) -> bool:
    labels = set(item.labels)
    if "reviewed" in labels:
        return False
    if record.status != "completed":
        return True
    if item.status != "in_progress":
        return True
    if not {"flow", "reviewing", "completed-by-agent"}.issubset(labels):
        return True
    if {"blocked", "running", "landed", "rejected", "review-blocked", "land-blocked"}.intersection(labels):
        return True
    if any(label.startswith("blocker-") for label in labels):
        return True
    return False


def _cheap_unstick_verification(
    root: Path,
    record: RunRecord,
    *,
    conflict_scan: bool | None = None,
    run_commands: bool = True,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    issues: list[str] = []
    raw = _read_result_payload(root, record)
    result = raw.get("result")
    if result is None:
        issues.append("missing or invalid result.json")
    else:
        if result.task_id != record.task_id:
            issues.append("result task_id does not match run record")
        if result.status != "completed":
            issues.append(f"result status is {result.status}, not completed")
        if result.blockers:
            issues.append("result has blockers")
        if result.unfinished:
            issues.append("result has unfinished work")

    verification_values = raw.get("verification_values", [])
    for command in verification_values:
        if _looks_like_recorded_failure(command):
            issues.append(f"recorded verification gap: {command}")

    if conflict_scan is None:
        conflict_scan = _scan_conflict_markers(root)
    if conflict_scan:
        issues.append("conflict markers remain in final tree")

    cheap_commands = tuple(command for command in verification_values if _is_cheap_verification_command(command))
    if run_commands and cheap_commands:
        issues.extend(_cheap_verification_issues(root, list(cheap_commands)))
    return tuple(issues), cheap_commands


def _read_result_payload(root: Path, record: RunRecord) -> dict[str, object]:
    path = result_path(root, record.task_id)
    if not path.exists():
        path = Path(record.result)
    if not path.exists():
        return {"result": None, "verification_values": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = WorkerResult.model_validate(data)
    except Exception:
        return {"result": None, "verification_values": []}
    values = data.get("verification", [])
    commands: list[str] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str):
                commands.append(value)
            elif isinstance(value, dict) and isinstance(value.get("command"), str):
                commands.append(value["command"])
                if value.get("status") == "failed":
                    commands.append(f"{value['command']} (failed)")
    return {"result": result, "verification_values": commands}


def _looks_like_recorded_failure(command: str) -> bool:
    lowered = command.lower()
    return "failed" in lowered or "err_" in lowered or "error:" in lowered


def _is_cheap_verification_command(command: str) -> bool:
    stripped = command.strip()
    if "(" in stripped or "failed" in stripped.lower():
        return False
    return (
        stripped.startswith("node --check ")
        or stripped.startswith("node --test ")
        or stripped.startswith("node -e ")
        or stripped.startswith("rg ")
    )


def _scan_conflict_markers(root: Path) -> bool:
    result = subprocess.run(
        ["git", "grep", "-n", "-E", r"^(<<<<<<<|=======|>>>>>>>)", "--", ".", ":(exclude).flow"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def _cheap_verification_issues(root: Path, commands: list[str]) -> list[str]:
    issues: list[str] = []
    normal_commands: list[str] = []
    for command in commands:
        if _is_no_match_rg_check(command):
            if _run_no_match_check(root, command):
                issues.append(f"cheap verification found matches: {command}")
        else:
            normal_commands.append(command)
    for command in normal_commands:
        if not _run_command_check(root, command):
            issues.append(f"cheap verification failed: {command}")
    return issues


def _is_no_match_rg_check(command: str) -> bool:
    stripped = command.strip()
    return stripped.startswith("rg ") and ("<<<<<<<" in stripped or ">>>>>>>" in stripped)


def _run_no_match_check(root: Path, command: str) -> bool:
    result = subprocess.run(
        command,
        cwd=root,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode == 0


def _run_command_check(root: Path, command: str) -> bool:
    result = subprocess.run(
        command,
        cwd=root,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode == 0


def _branch_is_safely_contained(root: Path, record: RunRecord) -> bool:
    try:
        if not is_ancestor(root, record.branch, "HEAD"):
            return False
        worktree = Path(record.worktree)
        return not worktree.exists() or not worktree_has_changes(worktree)
    except GitError:
        return False


def _print_unstick_candidates(candidates: list[UnstickCandidate], *, fix: bool) -> None:
    table = Table(title="c3x unstick")
    table.add_column("Task")
    table.add_column("Action")
    table.add_column("Run")
    table.add_column("Bead")
    table.add_column("Verification")
    table.add_column("Reason")
    for candidate in candidates:
        verification = "ok" if not candidate.verification_issues else f"{len(candidate.verification_issues)} gap(s)"
        table.add_row(
            candidate.task_id,
            candidate.action if fix else f"would {candidate.action}",
            candidate.record_status,
            candidate.bead_status or "",
            verification,
            candidate.reason,
        )
    console.print(table)
    for candidate in candidates:
        for issue in candidate.verification_issues:
            console.print(f"[yellow]{candidate.task_id} verification:[/yellow] {issue}")


def _apply_unstick_candidate(root: Path, beads: Beads, candidate: UnstickCandidate) -> None:
    if candidate.action == "mark-blocked-missing-run-record":
        beads.add_note(candidate.task_id, "c3x unstick found stale running state with no canonical run.json")
        beads.add_labels(candidate.task_id, ["flow", "blocked", "blocker-run-record-missing"])
        beads.remove_labels(candidate.task_id, ["running", "reviewing"])
        return

    if candidate.action in {"mark-completed-from-result", "mark-reviewed", "close-contained"}:
        cleanup_tasks = [task for task in _review_cleanup_tasks(beads, candidate.task_id) if task.status != "closed"]
        if cleanup_tasks:
            task_list = ", ".join(task.id for task in cleanup_tasks)
            raise ValueError(f"{candidate.task_id} still has review cleanup blockers: {task_list}")

    record = _load_repaired_current_run_record(root, candidate.task_id)
    worktree = Path(record.worktree)
    if worktree.exists() and worktree_has_changes(worktree):
        try:
            commit_worktree_changes(worktree, f"Save local changes before unsticking task {candidate.task_id}")
        except GitError as exc:
            raise ValueError(f"worker worktree has uncommitted changes that could not be saved: {exc}")
    item = beads.show(candidate.task_id)
    if candidate.action == "mark-completed-from-result":
        evidence = _completed_result_evidence(root, candidate.task_id)
        if evidence is None:
            raise ValueError(f"{candidate.task_id} has no completed result.json evidence")
        evidence_record, result = evidence
        result_text = Path(evidence_record.result).read_text(encoding="utf-8")
        _save_canonical_result(root, candidate.task_id, result_text)
        beads.add_note(candidate.task_id, f"c3x unstick recovered completed result: {result.summary}")
        beads.set_status(candidate.task_id, "in_progress")
        beads.add_labels(candidate.task_id, ["flow", "reviewing", "completed-by-agent"])
        for label in _completed_result_removed_labels(item):
            beads.remove_labels(candidate.task_id, [label])
        record = evidence_record.model_copy(
            update={
                "result": str(result_path(root, candidate.task_id)),
                "status": "completed",
                "outcome": "completed",
                "finished_at": evidence_record.finished_at or _now(),
            }
        )
        record.save(run_record_path(root, candidate.task_id))
        return
    if candidate.action == "mark-reviewed":
        beads.add_note(candidate.task_id, "c3x unstick repaired stale review state")
        beads.add_labels(candidate.task_id, ["reviewed", "reviewing"])
        beads.remove_labels(candidate.task_id, ["running", "blocked"])
        record.status = "reviewed"
        record.outcome = "reviewed"
        record.save(run_record_path(root, candidate.task_id))
        return
    if candidate.action in {"close-landed", "close-contained"}:
        beads.add_note(candidate.task_id, "c3x unstick closed stale active state after local evidence check")
        beads.close(candidate.task_id, "Closed stale c3x active state after local evidence check")
        record.status = "landed"
        record.outcome = "landed"
        if record.finished_at is None:
            record.finished_at = _now()
        record.save(run_record_path(root, candidate.task_id))
        return
    if candidate.action == "close-review-resolved":
        beads.add_note(candidate.task_id, "c3x unstick closed task after all direct review cleanup blockers closed")
        beads.close(candidate.task_id, "Resolved by closed review cleanup blockers")
        record.status = "landed"
        record.outcome = "review-resolved"
        record.finished_at = record.finished_at or _now()
        record.landing_branch = current_branch(root)
        record.landed_revision = rev_parse(root, "HEAD")
        record.save(run_record_path(root, candidate.task_id))
        return
    raise ValueError(f"unknown unstick action: {candidate.action}")


def _completed_result_removed_labels(item: BeadSummary) -> list[str]:
    labels = {
        "running",
        "blocked",
        "landed",
        "rejected",
        "review-blocked",
        "land-blocked",
    }
    labels.update(label for label in item.labels if label.startswith("blocker-"))
    return sorted(labels)


def _maybe_warn_stuck(root: Path, beads: Beads) -> None:
    candidates = [
        candidate
        for candidate in _unstick_candidates(root, beads, task_id=None, verify_mode="none")
        if candidate.action in {"close-landed", "close-contained"} and _stuck_candidate_is_old(root, candidate)
    ]
    if not candidates:
        return
    signature = ",".join(sorted(f"{candidate.task_id}:{candidate.action}" for candidate in candidates))
    notice = _read_stuck_notice(root)
    if notice.get("signature") == signature and not _notice_cooldown_elapsed(notice.get("updated_at", ""), minutes=30):
        return
    path = stuck_notice_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"signature": signature, "updated_at": _now()}, indent=2) + "\n", encoding="utf-8")
    task_list = ", ".join(candidate.task_id for candidate in candidates[:5])
    extra = "" if len(candidates) <= 5 else f" and {len(candidates) - 5} more"
    console.print(
        "\a[yellow]c3x may be stuck:[/yellow] "
        f"{len(candidates)} stale active task(s): {task_list}{extra}. "
        "Run `c3x unstick` to review evidence."
    )


def _stuck_candidate_is_old(root: Path, candidate: UnstickCandidate) -> bool:
    record = RunRecord.load(run_record_path(root, candidate.task_id))
    timestamp = record.finished_at or record.started_at
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() >= 600


def _read_stuck_notice(root: Path) -> dict[str, str]:
    path = stuck_notice_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str)}


def _notice_cooldown_elapsed(timestamp: str, *, minutes: int) -> bool:
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() >= minutes * 60


def _worker_process_targets(pid: int) -> list[int]:
    return [pid, *_descendant_pids(pid)]


def _kill_worker_process_tree(pid: int, *, force: bool) -> list[int]:
    sig = signal.SIGKILL if force else signal.SIGTERM
    targets = _worker_process_targets(pid)
    try:
        pgid = os.getpgid(pid)
        if pgid == pid:
            os.killpg(pgid, sig)
            return targets
    except ProcessLookupError:
        return []
    except PermissionError:
        pass
    for target in reversed(targets):
        try:
            os.kill(target, sig)
        except ProcessLookupError:
            continue
    return targets


def _descendant_pids(pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    proc = Path("/proc")
    if not proc.exists():
        return []
    for path in proc.iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text(encoding="utf-8")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        ppid = _stat_ppid(stat)
        if ppid is None:
            continue
        children.setdefault(ppid, []).append(int(path.name))
    descendants: list[int] = []
    stack = list(children.get(pid, []))
    while stack:
        child = stack.pop()
        descendants.append(child)
        stack.extend(children.get(child, []))
    return descendants


def _stat_ppid(stat: str) -> int | None:
    try:
        after_name = stat.rsplit(")", 1)[1].strip()
        fields = after_name.split()
        return int(fields[1])
    except (IndexError, ValueError):
        return None


def _run_cleanup_action(root: Path, action: CleanupAction, *, force: bool) -> None:
    if action.repair_run_record:
        _repair_archived_run_record_paths(action.run_dir / "run.json")
        return
    if action.repair_merge:
        commit_worktree_changes(action.worktree, f"Complete c3x task {action.task_id}")
        merge_branch(root, action.branch)
    cleanup_force = force or action.reason.startswith("landed") or action.remove_run_dir
    remove_worktree(root, action.worktree, force=cleanup_force)
    delete_branch(root, action.branch, force=cleanup_force)
    if action.remove_run_dir and action.run_dir.exists():
        shutil.rmtree(action.run_dir)


def _confirm_repair_merge(root: Path, action: CleanupAction) -> bool:
    console.print(f"[yellow]{action.task_id} is marked landed, but branch is not merged.[/yellow]")
    console.print(branch_diff_summary(root, action.branch))
    return typer.confirm(f"Merge {action.branch} before cleanup?", default=False)


def _auto_review(root: Path, beads: Beads) -> None:
    config = load_config(root)
    for item in _with_labels(beads.list_active(), {"flow", "reviewing"}):
        if "reviewed" in item.labels:
            continue
        result: WorkerResult | None = None
        record: RunRecord | None = None
        full_item: BeadSummary = item
        try:
            result = _load_worker_result(root, item.id)
            _review_result(result)
            record = _load_repaired_current_run_record(root, item.id)
            full_item = beads.show(item.id)
            _commit_worktree_before_review(record)
            review_result = run_reviewer(
                root,
                config,
                full_item,
                result,
                record=record,
                diff_summary=branch_diff_summary(root, record.branch),
            )
            _apply_review_result(root, beads, full_item, result, review_result, record=record)
            if review_result.status == "approved":
                console.print(f"[green]Reviewed[/green] {item.id}")
            else:
                console.print(f"[yellow]Review blocked[/yellow] {item.id}: {len(review_result.issues)} issue(s)")
        except (AgentError, BeadsError, GitError, ValueError) as exc:
            if result is not None and record is not None and not isinstance(exc, BeadsError):
                try:
                    _block_auto_review_exception(root, beads, full_item, result, record, exc)
                    console.print(f"[yellow]Review blocked[/yellow] {item.id}: {exc}")
                    continue
                except BeadsError:
                    pass
            beads.add_note(item.id, f"c3x auto-review blocked: {exc}")
            beads.add_labels(item.id, ["flow", "blocked", "review-blocked"])
            beads.remove_labels(item.id, ["reviewing"])
            console.print(f"[yellow]Review blocked[/yellow] {item.id}: {exc}")


def _commit_worktree_before_review(record: RunRecord) -> None:
    worktree = Path(record.worktree)
    if not worktree.exists():
        raise ValueError(f"worker worktree is missing: {worktree}")
    commit_worktree_changes(worktree, f"Complete c3x task {record.task_id}")


def _block_auto_review_exception(
    root: Path,
    beads: Beads,
    item: BeadSummary,
    worker_result: WorkerResult,
    record: RunRecord,
    exc: Exception,
) -> None:
    review_result = ReviewResult(
        task_id=item.id,
        status="blocked",
        summary=f"Auto-review could not run: {exc}",
        issues=[
            ReviewIssue(
                title="Resolve auto-review blocker",
                description=str(exc),
                severity="high",
            )
        ],
    )
    _block_review_with_tasks(beads, item, worker_result, review_result)
    record.status = "blocked"
    record.outcome = "review-blocked"
    record.save(run_record_path(root, item.id))


def _apply_review_result(
    root: Path,
    beads: Beads,
    item: BeadSummary,
    worker_result: WorkerResult,
    review_result: ReviewResult,
    *,
    record: RunRecord,
) -> None:
    if review_result.task_id != item.id:
        raise ValueError(f"Reviewer task id is '{review_result.task_id}', not '{item.id}'")
    if review_result.status == "approved" and review_result.issues:
        raise ValueError("Reviewer approved with unresolved issues")
    if review_result.status == "blocked" or review_result.issues:
        _block_review_with_tasks(beads, item, worker_result, review_result)
        record.status = "blocked"
        record.outcome = "review-blocked"
        record.save(run_record_path(root, item.id))
        return
    beads.add_note(item.id, f"c3x review passed: {review_result.summary or worker_result.summary}")
    beads.add_labels(item.id, ["reviewed", "reviewing"])
    beads.remove_labels(item.id, ["running", "blocked", "review-blocked", "blocker-review-issues"])
    record.status = "reviewed"
    record.outcome = "reviewed"
    record.save(run_record_path(root, item.id))


def _block_review_with_tasks(
    beads: Beads,
    item: BeadSummary,
    worker_result: WorkerResult,
    review_result: ReviewResult,
) -> None:
    issues = review_result.issues or [
        ReviewIssue(
            title=f"Resolve review blocker for {item.id}",
            description=review_result.summary or "Reviewer blocked landing without a structured issue.",
            severity="high",
        )
    ]
    created_ids: list[str] = []
    for issue in issues:
        created = beads.create_task(
            _review_issue_title(item, issue),
            description=_review_issue_description(item, worker_result, review_result, issue),
            labels=["flow", "ready", "review-fix"],
            issue_type="task",
            priority=0,
        )
        child_id = str(created.get("id", ""))
        if child_id:
            created_ids.append(child_id)
            beads.add_blocker(child_id, item.id)
    task_list = ", ".join(created_ids) if created_ids else "no cleanup task id returned"
    beads.add_note(
        item.id,
        (
            f"c3x review blocked: {review_result.summary or 'review issues found'}\n\n"
            f"Cleanup tasks blocking this item: {task_list}"
        ),
    )
    beads.add_labels(item.id, ["flow", "blocked", "review-blocked", "blocker-review-issues"])
    beads.remove_labels(item.id, ["running", "reviewing", "reviewed"])


def _review_issue_title(item: BeadSummary, issue: ReviewIssue) -> str:
    return f"Fix review issue for {item.id}: {issue.title}"


def _review_issue_description(
    item: BeadSummary,
    worker_result: WorkerResult,
    review_result: ReviewResult,
    issue: ReviewIssue,
) -> str:
    requirements = "\n".join(
        f"- [{requirement.status}] {requirement.requirement}: {requirement.evidence}"
        for requirement in review_result.requirements
    )
    parts = [
        f"Review cleanup for blocked task {item.id}: {item.title}.",
        f"Severity: {issue.severity}",
        "",
        issue.description or issue.title,
        "",
        f"Reviewer summary: {review_result.summary or 'n/a'}",
        f"Worker summary: {worker_result.summary or 'n/a'}",
    ]
    if requirements:
        parts.extend(["", "Requirement review:", requirements])
    parts.extend(["", f"Blocks: {item.id}"])
    return "\n".join(parts).strip()


def _auto_land(root: Path, beads: Beads, *, cleanup_done: bool) -> None:
    if worktree_has_changes(root, ignored_prefixes=(".c3x/", ".flow/")):
        _write_activity_event(root, "not landing", "root worktree has uncommitted changes")
        return
    for item in _with_labels(beads.list_active(), {"flow", "reviewing", "reviewed"}):
        try:
            record = _load_repaired_current_run_record(root, item.id)
            if record.status != "reviewed":
                continue
            _land_record(root, beads, record, cleanup_done=cleanup_done, close_note="Landed by c3x watch")
            console.print(f"[green]Landed[/green] {item.id}")
            if cleanup_done:
                console.print(f"[green]Cleaned[/green] {item.id}")
        except GitMergeConflict as exc:
            _mark_land_blocked(beads, item.id, exc)
            console.print(f"[yellow]Land blocked[/yellow] {item.id}: {exc}")
        except (BeadsError, GitError, ValueError) as exc:
            beads.add_note(item.id, f"c3x auto-land blocked: {exc}")
            beads.add_labels(item.id, ["flow", "blocked", "land-blocked"])
            console.print(f"[yellow]Land blocked[/yellow] {item.id}: {exc}")


def _mark_land_blocked(beads: Beads, task_id: str, exc: GitMergeConflict) -> None:
    files = "\n".join(f"- {path}" for path in exc.files) or "- unknown"
    beads.add_note(
        task_id,
        (
            f"c3x land blocked by merge conflict in {exc.branch}.\n\n"
            f"Conflicted files:\n{files}\n\n"
            f"{exc.detail}"
        ).strip(),
    )
    beads.add_labels(task_id, ["flow", "blocked", "land-blocked", "blocker-merge-conflict"])
    beads.remove_labels(task_id, ["running"])


def _mark_land_error(beads: Beads, task_id: str, exc: Exception) -> None:
    beads.add_note(task_id, f"c3x land blocked: {exc}")
    beads.add_labels(task_id, ["flow", "blocked", "land-blocked"])


def _critic_tick(beads: Beads) -> str:
    blocked = _with_labels(beads.list_active(), {"flow", "blocked"})
    if len(blocked) < 2:
        return "critic tasks OK"
    existing = [
        item
        for item in beads.list_active()
        if {"flow", "critic"}.issubset(set(item.labels))
        and "blocked tasks need investigation" in item.title.lower()
    ]
    if existing:
        return "critic task already exists"
    created = beads.create_task(
        "Blocked tasks need investigation",
        description=(
            f"c3x critic found {len(blocked)} blocked tasks.\n\n"
            "Look for missing fixtures, setup documentation, verification commands, "
            "or overly broad scopes."
        ),
        labels=["flow", "critic", "ready"],
        issue_type="task",
        priority=1,
    )
    return f"created critic task {created.get('id', '<unknown>')}"


def _recover_interrupted_workers(root: Path, beads: Beads) -> None:
    config = load_config(root)
    for record in _canonical_run_records(root):
        if record.status != "running":
            continue
        if Path(record.result).exists():
            continue
        if record.pid is not None and _process_is_running(record.pid):
            continue
        try:
            if _is_transient_worker_failure(record):
                _, mode_used = _retry_task(
                    root,
                    config,
                    beads,
                    record.task_id,
                    retry_mode=_supervisor_retry_mode(record),
                )
                action = "Resumed session" if mode_used == "session" else "Continued worktree"
                console.print(f"[yellow]{action}[/yellow] {record.task_id}")
            else:
                _block_missing_worker_result(root, beads, record)
        except (AgentError, BeadsError, GitError, ValueError) as exc:
            beads.add_note(record.task_id, f"c3x could not restart interrupted worker: {exc}")
            beads.add_labels(record.task_id, ["flow", "blocked", "blocker-restart-failed"])
            beads.remove_labels(record.task_id, ["running", "reviewing"])
            record.status = "blocked"
            record.outcome = "restart-failed"
            record.finished_at = _now()
            record.save(run_record_path(root, record.task_id))
            console.print(f"[yellow]Restart blocked[/yellow] {record.task_id}: {exc}")


def _import_finished_results(root: Path, beads: Beads) -> None:
    for record in _canonical_run_records(root):
        if record.status != "running":
            continue
        result_file = _result_file_for_record(record)
        if not result_file.exists():
            if record.pid is not None and not _process_is_running(record.pid):
                _block_missing_worker_result(root, beads, record)
            continue
        record.result = str(result_file)
        worktree = _worktree_from_result_path(result_file)
        if worktree is not None:
            record.worktree = str(worktree)
            branch = _branch_for_worktree(worktree)
            if branch:
                record.branch = branch
        record.attempt = _record_attempt(record)
        result_text = result_file.read_text(encoding="utf-8")
        result = WorkerResult.model_validate_json(result_text)
        if result.task_id != record.task_id:
            _try_beads_write(
                f"record rejected result for {record.task_id}",
                lambda: beads.add_note(record.task_id, "Worker result rejected: task id mismatch"),
            )
            _try_beads_write(
                f"mark {record.task_id} rejected",
                lambda: beads.add_labels(record.task_id, ["flow", "blocked", "rejected", "blocker-result-schema"]),
            )
            record.status = "blocked"
            record.outcome = "rejected"
        elif result.status == "completed":
            _save_canonical_result(root, record.task_id, result_text)
            if record.task_type == "conflict_resolver":
                try:
                    commit_worktree_changes(Path(record.worktree), f"Resolve merge conflicts for c3x task {record.task_id}")
                except GitError as exc:
                    console.print(f"[yellow]Failed to commit conflict resolver changes[/yellow] {record.task_id}: {exc}")
            _try_beads_write(
                f"record completed result for {record.task_id}",
                lambda: beads.add_note(record.task_id, _result_note(result)),
            )
            _try_beads_write(
                f"mark {record.task_id} reviewing",
                lambda: beads.add_labels(record.task_id, ["flow", "reviewing", "completed-by-agent"]),
            )
            _try_beads_write(
                f"remove running labels from {record.task_id}",
                lambda: beads.remove_labels(record.task_id, ["running", "blocked"]),
            )
            record.status = "completed"
            record.outcome = "completed"
        else:
            _save_canonical_result(root, record.task_id, result_text)
            _try_beads_write(
                f"record blocked result for {record.task_id}",
                lambda: beads.add_note(record.task_id, _result_note(result)),
            )
            category = result.blocker_category or "unknown"
            _try_beads_write(
                f"mark {record.task_id} blocked",
                lambda: beads.add_labels(record.task_id, ["flow", "blocked", f"blocker-{category}"]),
            )
            _try_beads_write(
                f"remove running labels from {record.task_id}",
                lambda: beads.remove_labels(record.task_id, ["running", "reviewing"]),
            )
            record.status = "blocked"
            record.outcome = result.status
        record.finished_at = _now()
        record.save(run_record_path(root, record.task_id))


def _result_file_for_record(record: RunRecord) -> Path:
    expected = Path(record.result)
    if expected.exists():
        return expected
    reported = _reported_result_path(Path(record.last_message))
    if reported and reported.exists():
        return reported
    return expected


def _save_canonical_result(root: Path, task_id: str, result_text: str) -> None:
    path = result_path(root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result_text, encoding="utf-8")


def _block_missing_worker_result(root: Path, beads: Beads, record: RunRecord) -> None:
    evidence = _missing_result_evidence(record)
    note = (
        "Worker exited without writing result.json.\n"
        "The supervisor marked this task blocked so it can be retried or fixed by a user.\n\n"
        f"{evidence}"
    )
    note_saved = _try_beads_write(
        f"record missing-result note for {record.task_id}",
        lambda: beads.add_note(record.task_id, note),
    )
    labels_added = _try_beads_write(
        f"mark {record.task_id} blocked",
        lambda: beads.add_labels(record.task_id, ["flow", "blocked", "blocker-result-missing"]),
    )
    labels_removed = _try_beads_write(
        f"remove running labels from {record.task_id}",
        lambda: beads.remove_labels(record.task_id, ["running", "reviewing"]),
    )
    record.status = "blocked"
    record.outcome = "missing-result"
    record.finished_at = _now()
    record.save(run_record_path(root, record.task_id))
    if note_saved and labels_added and labels_removed:
        console.print(f"[yellow]Blocked[/yellow] {record.task_id}: worker exited without result.json")
    else:
        console.print(
            f"[yellow]Blocked locally[/yellow] {record.task_id}: worker exited without result.json; "
            "some Beads updates failed"
        )


def _try_beads_write(action: str, write: Callable[[], None]) -> bool:
    try:
        write()
    except BeadsError as exc:
        console.print(f"[yellow]warning:[/yellow] could not {action}: {_beads_error_summary(exc)}")
        return False
    return True


def _beads_error_summary(exc: BeadsError) -> str:
    message = _one_line(str(exc))
    lower = message.lower()
    if "too large for column 'old_value'" in lower:
        return "Beads rejected the update because the existing issue payload is too large for its event log."
    if "too large" in lower:
        return "Beads rejected the update because the issue payload is too large."
    return message[:500]


def _missing_result_evidence(record: RunRecord) -> str:
    last_message_path = Path(record.last_message)
    stderr_path = Path(record.prompt).parent / "stderr.log"
    lines = [
        f"pid: {record.pid}",
        f"attempt: {record.attempt}",
        f"expected_result: {record.result}",
        f"last_message_path: {last_message_path}",
        f"stderr_path: {stderr_path}",
        f"summary: {_missing_result_summary(record, last_message_path=last_message_path, stderr_path=stderr_path)}",
    ]
    return "\n\n".join(lines)


def _missing_result_summary(record: RunRecord, *, last_message_path: Path, stderr_path: Path) -> str:
    last_message = _read_tail(last_message_path, max_chars=2000)
    stderr = _read_tail(stderr_path, max_chars=12000)
    combined = f"{last_message}\n{stderr}".lower()
    if _has_usage_limit_evidence(combined):
        return "Codex usage limit stopped the worker before c3x found result.json."
    if "rate limit" in combined or "429" in combined:
        return "Codex rate limit stopped the worker before c3x found result.json."
    if "failed to lookup address information" in combined or "failed to connect" in combined:
        return "Network/DNS failure stopped the worker before c3x found result.json."
    if "stream disconnected before completion" in combined or "error sending request" in combined:
        return "Codex stream disconnected before c3x found result.json."
    if (
        re.search(r"wrote\s+\[`\.c3x/[^`\]]*-?result\.json`\]", combined, flags=re.IGNORECASE)
        or re.search(r"wrote\s+\.c3x/[^`\s]*-?result\.json", combined, flags=re.IGNORECASE)
        or "session result saved at" in combined
    ):
        return (
            "Worker reported writing result.json, but not at the expected path. "
            "Check last_message_path and stderr_path for the reported location."
        )
    if last_message:
        return "Worker produced a final message, but c3x did not find result.json at expected_result."
    if stderr:
        return "Worker stderr exists, but c3x did not find result.json at expected_result."
    return "Worker exited without writing result.json; see paths above for available logs."


def _read_tail(path: Path, *, max_chars: int) -> str:
    if not path.exists():
        return ""
    byte_window = max(max_chars * 4, 4096)
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(size - byte_window, 0))
        text = handle.read().decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text.strip()
    return text[-max_chars:].strip()


def _has_usage_limit_evidence(text: str) -> bool:
    return "usage limit" in text.lower()


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_worker_records(root: Path, *, canonical_records: list[RunRecord] | None = None) -> list[RunRecord]:
    if canonical_records is None:
        canonical_records = _canonical_run_records(root)
    return [
        record
        for record in canonical_records
        if record.status == "running"
        and record.pid is not None
        and _process_is_running(record.pid)
    ]


def _load_worker_result(root: Path, task_id: str) -> WorkerResult:
    path = result_path(root, task_id)
    current_attempt = 1
    record: RunRecord | None = None
    if not path.exists():
        record_path = run_record_path(root, task_id)
        if record_path.exists():
            record = _load_repaired_current_run_record(root, task_id)
            current_attempt = _record_attempt(record)
            path = _result_file_for_record(record)
    if not path.exists():
        completed_evidence = _completed_result_evidence(root, task_id)
        if completed_evidence is not None and _record_attempt(completed_evidence[0]) >= current_attempt:
            evidence_record, result = completed_evidence
            _save_canonical_result(root, task_id, Path(evidence_record.result).read_text(encoding="utf-8"))
            return result
    if not path.exists() and record is not None:
        synthesized = _missing_result_worker_result(root, record)
        if synthesized is not None:
            _save_canonical_result(root, task_id, synthesized.model_dump_json(indent=2) + "\n")
            return synthesized
    if not path.exists():
        raise ValueError(f"missing worker result: {path}")
    return WorkerResult.model_validate_json(path.read_text(encoding="utf-8"))


def _missing_result_worker_result(root: Path, record: RunRecord) -> WorkerResult | None:
    last_message = _read_tail(Path(record.last_message), max_chars=4000)
    if not last_message:
        return None
    blockers = [
        f"Worker final message exists but result.json is missing at {record.result}.",
        f"Last message excerpt: {_compact_excerpt(last_message, limit=1000)}",
    ]
    if _scan_conflict_markers(Path(record.worktree)):
        blockers.insert(0, "Conflict markers remain in the worker worktree.")
    return WorkerResult(
        task_id=record.task_id,
        status="blocked",
        summary="Worker finished without writing result.json.",
        task_kind="merge-conflict" if "conflict" in record.branch else None,
        attempt=record.attempt,
        blockers=blockers,
        blocker_category="merge-conflict" if "conflict" in record.branch else "result-missing",
        confidence="low",
        unfinished=[],
    )


def _review_result(result: WorkerResult) -> None:
    if result.status != "completed":
        raise ValueError(f"Worker status is '{result.status}', not 'completed'")
    for command in result.verification:
        if command.status == "failed" or (command.exit_code is not None and command.exit_code != 0):
            raise ValueError(f"Verification failed: {command.command} failed")
        if command.status == "skipped":
            raise ValueError(f"Verification failed: {command.command} was skipped")



def _print_verification(results: list) -> None:
    table = Table(title="c3x verify")
    table.add_column("Command")
    table.add_column("Status")
    table.add_column("Exit", justify="right")
    table.add_column("Log")
    for result in results:
        table.add_row(result.command, result.status, "" if result.exit_code is None else str(result.exit_code), result.log_path or "")
    console.print(table)


def _print_counter(title: str, values: dict) -> None:
    table = Table(title=title)
    table.add_column("Name")
    table.add_column("Count", justify="right")
    for name, count in sorted(values.items()):
        table.add_row(str(name), str(count))
    console.print(table)


def _run_records(root: Path) -> list[RunRecord]:
    return [record for _, record in _run_record_paths(root)]


def _canonical_run_records(root: Path) -> list[RunRecord]:
    return [record for _, record in _canonical_run_record_paths(root)]


def _canonical_run_record_paths(root: Path) -> list[tuple[Path, RunRecord]]:
    return [
        (path, record)
        for path, record in _run_record_paths(root)
        if path == run_record_path(root, record.task_id)
    ]


def _run_record_paths(root: Path) -> list[tuple[Path, RunRecord]]:
    records = []
    for path in sorted((root / FLOW_DIR / "runs").glob("*/run.json")):
        records.append((path, RunRecord.load(path)))
    return records


def _with_labels(items: list[BeadSummary], labels: set[str]) -> list[BeadSummary]:
    return [item for item in items if labels.issubset(set(item.labels))]


def _blocked_reason(item: BeadSummary) -> str:
    categories = [
        label.removeprefix("blocker-").replace("_", " ").replace("-", " ")
        for label in sorted(item.labels)
        if label.startswith("blocker-")
    ]
    note_reason = _blocked_note_reason(item.notes or "")
    if categories and note_reason:
        return f"{', '.join(categories)}: {note_reason}"
    if categories:
        return ", ".join(categories)
    return note_reason or "blocked label present"


def _blocked_note_reason(notes: str) -> str:
    lines = [line.strip() for line in notes.splitlines() if line.strip()]
    lowered = notes.lower()
    if _has_usage_limit_evidence(notes):
        return "Codex usage limit; worker exited without result.json"
    if "worker exited without writing result.json" in lowered:
        return "Worker exited without writing result.json"
    for line in reversed(lines):
        line_lower = line.lower()
        if line_lower.startswith(("worker blocked:", "worker failed:")):
            return line
        if line_lower.startswith("c3x auto-review blocked:"):
            return line.removeprefix("c3x auto-review blocked:").strip()
        if line_lower.startswith("c3x auto-land blocked:"):
            return line.removeprefix("c3x auto-land blocked:").strip()
        if "c3x land blocked by merge conflict" in line_lower:
            return line
        if "could not restart interrupted worker" in line_lower:
            return line
    return ""


def _warn_if_risky_flow_branch(root: Path) -> None:
    branch = current_branch(root)
    if branch not in {"main", "master", "HEAD"}:
        return
    console.print(
        "[yellow]warning:[/yellow] "
        f"c3x root worktree is on `{branch}`. Task branches will fork from and merge back into this branch."
    )


def _result_note(result: WorkerResult) -> str:
    lines = [
        f"Worker {result.status}: {result.summary}",
        f"task_kind: {result.task_kind or 'unspecified'}",
        f"attempt: {result.attempt or 'unspecified'}",
        f"confidence: {result.confidence or 'unspecified'}",
    ]
    if result.blocker_category:
        lines.append(f"blocker_category: {result.blocker_category}")
    if result.blockers:
        lines.append("blockers:\n- " + "\n- ".join(result.blockers))
    if result.unfinished:
        lines.append("unfinished:\n- " + "\n- ".join(result.unfinished))
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(message: str) -> int:
    console.print(f"[red]error:[/red] {message}")
    return 1


if __name__ == "__main__":
    app()
