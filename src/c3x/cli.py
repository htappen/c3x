from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table

from c3x.agent import AgentError, start_conflict_resolver, start_worker
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
    is_ancestor,
    merge_branch,
    remove_worktree,
    rev_parse,
    squash_head_to,
)
from c3x.metrics import collect_metrics
from c3x.paths import activity_path, pause_path, result_path, run_record_path
from c3x.schema import RunRecord, WorkerResult
from c3x.verify import run_verification


app = typer.Typer(
    name="c3x",
    help="Local agentic coding supervisor for Codex and Beads.",
    no_args_is_help=True,
)
console = Console()


@dataclass(frozen=True)
class CleanupAction:
    task_id: str
    run_dir: Path
    worktree: Path
    branch: str
    reason: str
    remove_run_dir: bool = False
    repair_merge: bool = False


@dataclass(frozen=True)
class SquashPlan:
    task_id: str
    base: str
    commits: tuple[str, ...]
    message: str


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
            for item in _beads(root).list_open()
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
    with Live(view, console=console, refresh_per_second=4) as live:
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
    with Live(_build_status_view(root), console=console, refresh_per_second=4) as live:
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
            _write_activity(root, f"waiting {interval}s before next tick")
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
    with Live(_build_status_view(root), console=console, refresh_per_second=4) as live:
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
            _write_activity(root, f"waiting {interval}s before next tick")
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
) -> None:
    """Start a fresh worker attempt for blocked or stale work."""
    root = _root()
    _warn_if_risky_flow_branch(root)
    config = load_config(root)
    beads = _beads(root)
    try:
        _import_finished_results(root, beads)
        task_ids = _retry_task_ids(beads, task_id=task_id, all_tasks=all_tasks)
        for item_id in task_ids:
            record = _retry_task(root, config, beads, item_id)
            console.print(f"[green]Retried[/green] {item_id} as attempt {record.attempt}")
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
        result = _load_worker_result(root, task_id)
        _review_result(result)
        beads = _beads(root)
        beads.add_note(task_id, f"c3x review passed: {result.summary}")
        beads.add_labels(task_id, ["reviewed", "reviewing"])
        beads.remove_labels(task_id, ["running", "blocked"])
        record = RunRecord.load(run_record_path(root, task_id))
        record.status = "reviewed"
        record.outcome = "reviewed"
        record.save(run_record_path(root, task_id))
    except (BeadsError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Reviewed[/green] {task_id}")


@app.command()
def land(
    task_id: Annotated[str, typer.Argument(help="Reviewed task id to merge.")],
    cleanup_done: Annotated[
        bool,
        typer.Option("--cleanup/--no-cleanup", help="Remove the landed worktree and branch after merge."),
    ] = True,
) -> None:
    """Merge a reviewed task branch and close the bead."""
    root = _root()
    _warn_if_risky_flow_branch(root)
    try:
        record = RunRecord.load(run_record_path(root, task_id))
        if record.status != "reviewed":
            raise ValueError(f"{task_id} is not reviewed")
        beads = _beads(root)
        commit_worktree_changes(Path(record.worktree), f"Complete c3x task {task_id}")
        merge_branch(root, record.branch)
        beads.close(task_id, "Landed by c3x")
        beads.add_labels(task_id, ["landed"])
        commit_ledger_changes(root, f"Close c3x task {task_id}")
        record.status = "landed"
        record.outcome = "landed"
        record.finished_at = _now()
        record.save(run_record_path(root, task_id))
        if cleanup_done:
            remove_worktree(root, Path(record.worktree), force=True)
            delete_branch(root, record.branch)
    except GitMergeConflict as exc:
        beads = _beads(root)
        _mark_land_blocked(beads, task_id, exc)
        raise typer.Exit(_error(str(exc))) from exc
    except (BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Landed[/green] {task_id}")
    if cleanup_done:
        console.print(f"[green]Cleaned[/green] {task_id}")


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
) -> None:
    """Remove landed task worktrees and superseded stale attempts."""
    root = _root()
    try:
        actions = _cleanup_actions(root, task_id=task_id)
        if not actions:
            console.print("[green]Nothing to clean.[/green]")
            return
        for action in actions:
            if dry_run:
                console.print(f"[yellow]Would clean[/yellow] {action.reason}: {action.task_id}")
                continue
            if action.repair_merge and not _confirm_repair_merge(root, action):
                console.print(f"[yellow]Skipped[/yellow] {action.task_id}")
                continue
            _run_cleanup_action(root, action, force=force)
            console.print(f"[green]Cleaned[/green] {action.reason}: {action.task_id}")
    except (GitError, ValueError) as exc:
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


def _build_status_view(root: Path) -> Group:
    return Group(_build_activity_table(root), _build_status_table(root), _build_workers_table(root))


def _build_activity_table(root: Path) -> Table:
    activity = _read_activity(root)
    supervisor = activity.get("supervisor") or "idle; no supervisor activity recorded"
    updated_at = activity.get("updated_at") or ""
    age = _age_label(updated_at) if updated_at else ""

    table = Table(title="c3x activity")
    table.add_column("Actor")
    table.add_column("Activity")
    table.add_column("Updated")
    table.add_row("Supervisor", supervisor, age)
    return table


def _build_workers_table(root: Path) -> Table:
    table = Table(title="c3x workers")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("PID", justify="right")
    table.add_column("Age")
    table.add_column("Latest")
    records = _run_records(root)
    active = [record for record in records if record.status in {"running", "completed", "reviewed"}]
    if not active:
        table.add_row("-", "idle", "", "", "")
        return table
    for record in active:
        latest = _one_line(_read_tail(Path(record.last_message), max_chars=180))
        table.add_row(
            record.task_id,
            record.status,
            "" if record.pid is None else str(record.pid),
            _age_label(record.started_at),
            latest,
        )
    return table


def _build_status_table(root: Path) -> Table:
    config = load_config(root)
    beads = _beads(root)
    open_items = beads.list_active()
    ready_items = beads.ready()
    inbox_items = _with_labels(open_items, {"flow", "inbox", "idea"})
    question_items = _with_labels(open_items, {"flow", "question"})
    running_items = _with_labels(open_items, {"flow", "running"})
    reviewing_items = _with_labels(open_items, {"flow", "reviewing"})
    blocked_items = _with_labels(open_items, {"flow", "blocked"})

    table = Table(title="c3x status")
    table.add_column("Bucket")
    table.add_column("Count", justify="right")
    table.add_row("Inbox", str(len(inbox_items)))
    table.add_row("Questions", str(len(question_items)))
    table.add_row("Active", str(len(open_items)))
    table.add_row("Ready", str(len(ready_items)))
    table.add_row("Running", str(len(running_items)))
    table.add_row("Reviewing", str(len(reviewing_items)))
    table.add_row("Blocked", str(len(blocked_items)))
    table.add_row("Max parallel workers", str(config.limits.max_parallel_workers))
    return table


def _write_activity(root: Path, supervisor: str) -> None:
    path = activity_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"supervisor": supervisor, "updated_at": _now()}, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_activity(root: Path) -> dict[str, str]:
    path = activity_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"supervisor": "activity state is unreadable", "updated_at": ""}
    return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str)}


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
    _write_activity(root, "importing finished worker results")
    _import_finished_results(root, beads)
    _write_activity(root, "planning inbox items")
    _plan_inbox(root, beads)
    _write_activity(root, "checking critic tasks")
    _critic_tick(beads)
    if dispatch:
        _write_activity(root, "checking worker capacity")
        config = load_config(root)
        running = len(_with_labels(beads.list_active(), {"flow", "running"}))
        slots = max(config.limits.max_parallel_workers - running, 0)
        for task in beads.ready()[:slots]:
            if "flow" in task.labels:
                _write_activity(root, f"dispatching worker {task.id}")
                start_worker(root, config, task)
                beads.set_status(task.id, "in_progress")
                beads.add_labels(task.id, ["flow", "running"])
                beads.remove_labels(task.id, ["ready", "blocked", "reviewing"])
    if review:
        _write_activity(root, "reviewing completed work")
        _auto_review(root, beads)
    if land:
        _write_activity(root, "landing reviewed work")
        _auto_land(root, beads, cleanup_done=cleanup_done)
    if resolve_conflicts:
        _write_activity(root, "checking merge-conflict blockers")
        config = load_config(root)
        for item_id in _conflict_task_ids(beads, task_id=None, all_tasks=True):
            _write_activity(root, f"starting conflict resolver {item_id}")
            _resolve_conflict_task(root, config, beads, item_id)
            console.print(f"[green]Conflict resolver started[/green] {item_id}")
    _write_activity(root, "tick complete")


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


def _conflict_task_ids(beads: Beads, *, task_id: str | None, all_tasks: bool) -> list[str]:
    if all_tasks and task_id:
        raise ValueError("pass either a task id or --all, not both")
    if not all_tasks and not task_id:
        raise ValueError("pass a task id or --all")
    if task_id:
        task = beads.show(task_id)
        if not {"flow", "blocked", "land-blocked", "blocker-merge-conflict"}.issubset(set(task.labels)):
            raise ValueError(f"{task_id} is not blocked on a merge conflict")
        return [task_id]
    blocked = _with_labels(beads.list_active(), {"flow", "blocked", "land-blocked", "blocker-merge-conflict"})
    return [item.id for item in blocked]


def _retry_task(root: Path, config: object, beads: Beads, task_id: str) -> RunRecord:
    task = beads.show(task_id)
    _ensure_retryable(root, task_id)
    _archive_current_run(root, task_id)
    beads.set_status(task_id, "open")
    beads.add_labels(task_id, ["flow", "ready"])
    beads.remove_labels(task_id, _retry_removed_labels(task))
    record = start_worker(root, config, task)
    beads.set_status(task_id, "in_progress")
    beads.add_labels(task_id, ["flow", "running", f"attempt-{record.attempt}"])
    beads.remove_labels(task_id, ["ready", "reviewing", "blocked"])
    beads.add_note(task_id, f"c3x retry started attempt {record.attempt}")
    return record


def _resolve_conflict_task(root: Path, config: object, beads: Beads, task_id: str) -> RunRecord:
    task = beads.show(task_id)
    _ensure_retryable(root, task_id)
    previous = RunRecord.load(run_record_path(root, task_id))
    original_result = _read_original_result(root, task_id)
    target_branch = current_branch(root)
    target_revision = rev_parse(root, "HEAD")
    _archive_current_run(root, task_id)
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
    )
    beads.set_status(task_id, "in_progress")
    beads.add_labels(task_id, ["flow", "running", "conflict-resolver", f"attempt-{record.attempt}"])
    beads.remove_labels(task_id, ["ready", "reviewing", "blocked"])
    beads.add_note(task_id, f"c3x conflict resolver started attempt {record.attempt}")
    return record


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


def _archive_current_run(root: Path, task_id: str) -> None:
    run_dir = run_record_path(root, task_id).parent
    if not run_dir.exists():
        return
    try:
        record = RunRecord.load(run_dir / "run.json")
        suffix = f"attempt-{record.attempt}"
    except Exception:
        suffix = "previous"
    target = _unique_archive_path(run_dir.with_name(f"{task_id}-{suffix}"))
    run_dir.rename(target)


def _unique_archive_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1


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


def _cleanup_actions(root: Path, *, task_id: str | None) -> list[CleanupAction]:
    records = _run_record_paths(root)
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
        if path == run_record_path(root, record.task_id):
            if record.status == "landed":
                try:
                    merged = is_ancestor(root, record.branch, "HEAD")
                except GitError as exc:
                    if not _is_missing_ref_error(exc):
                        raise
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
    if task_id and not actions:
        current = canonical.get(task_id)
        if current and current.status != "landed":
            raise ValueError(f"{task_id} is not landed and has no superseded attempts")
    return actions


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


def _run_cleanup_action(root: Path, action: CleanupAction, *, force: bool) -> None:
    if action.repair_merge:
        commit_worktree_changes(action.worktree, f"Complete c3x task {action.task_id}")
        merge_branch(root, action.branch)
    remove_worktree(root, action.worktree, force=force or action.reason.startswith("landed"))
    delete_branch(root, action.branch, force=force)
    if action.remove_run_dir and action.run_dir.exists():
        shutil.rmtree(action.run_dir)


def _confirm_repair_merge(root: Path, action: CleanupAction) -> bool:
    console.print(f"[yellow]{action.task_id} is marked landed, but branch is not merged.[/yellow]")
    console.print(branch_diff_summary(root, action.branch))
    return typer.confirm(f"Merge {action.branch} before cleanup?", default=False)


def _auto_review(root: Path, beads: Beads) -> None:
    for item in _with_labels(beads.list_active(), {"flow", "reviewing"}):
        if "reviewed" in item.labels:
            continue
        try:
            result = _load_worker_result(root, item.id)
            _review_result(result)
            beads.add_note(item.id, f"c3x auto-review passed: {result.summary}")
            beads.add_labels(item.id, ["reviewed"])
            record = RunRecord.load(run_record_path(root, item.id))
            record.status = "reviewed"
            record.outcome = "reviewed"
            record.save(run_record_path(root, item.id))
            console.print(f"[green]Reviewed[/green] {item.id}")
        except (BeadsError, ValueError) as exc:
            beads.add_note(item.id, f"c3x auto-review blocked: {exc}")
            beads.add_labels(item.id, ["flow", "blocked", "review-blocked"])
            beads.remove_labels(item.id, ["reviewing"])
            console.print(f"[yellow]Review blocked[/yellow] {item.id}: {exc}")


def _auto_land(root: Path, beads: Beads, *, cleanup_done: bool) -> None:
    for item in _with_labels(beads.list_active(), {"flow", "reviewing", "reviewed"}):
        try:
            record = RunRecord.load(run_record_path(root, item.id))
            if record.status != "reviewed":
                continue
            commit_worktree_changes(Path(record.worktree), f"Complete c3x task {item.id}")
            merge_branch(root, record.branch)
            beads.close(item.id, "Landed by c3x watch")
            beads.add_labels(item.id, ["landed"])
            commit_ledger_changes(root, f"Close c3x task {item.id}")
            record.status = "landed"
            record.outcome = "landed"
            record.finished_at = _now()
            record.save(run_record_path(root, item.id))
            console.print(f"[green]Landed[/green] {item.id}")
            if cleanup_done:
                remove_worktree(root, Path(record.worktree), force=True)
                delete_branch(root, record.branch)
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


def _critic_tick(beads: Beads) -> None:
    blocked = _with_labels(beads.list_active(), {"flow", "blocked"})
    if len(blocked) < 2:
        return
    existing = [
        item
        for item in beads.list_active()
        if {"flow", "critic"}.issubset(set(item.labels))
        and "blocked tasks need investigation" in item.title.lower()
    ]
    if existing:
        return
    beads.create_task(
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


def _import_finished_results(root: Path, beads: Beads) -> None:
    for record in _run_records(root):
        if record.status != "running":
            continue
        result_file = Path(record.result)
        if not result_file.exists():
            if record.pid is not None and not _process_is_running(record.pid):
                _block_missing_worker_result(root, beads, record)
            continue
        result_text = result_file.read_text(encoding="utf-8")
        result = WorkerResult.model_validate_json(result_text)
        if result.task_id != record.task_id:
            beads.add_note(record.task_id, "Worker result rejected: task id mismatch")
            beads.add_labels(record.task_id, ["flow", "blocked", "rejected", "blocker-result-schema"])
            record.status = "blocked"
            record.outcome = "rejected"
        elif result.status == "completed":
            _save_canonical_result(root, record.task_id, result_text)
            beads.add_note(record.task_id, _result_note(result))
            beads.add_labels(record.task_id, ["flow", "reviewing", "completed-by-agent"])
            beads.remove_labels(record.task_id, ["running", "blocked"])
            record.status = "completed"
            record.outcome = "completed"
        else:
            _save_canonical_result(root, record.task_id, result_text)
            beads.add_note(record.task_id, _result_note(result))
            category = result.blocker_category or "unknown"
            beads.add_labels(record.task_id, ["flow", "blocked", f"blocker-{category}"])
            beads.remove_labels(record.task_id, ["running", "reviewing"])
            record.status = "blocked"
            record.outcome = result.status
        record.finished_at = _now()
        record.save(run_record_path(root, record.task_id))


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
    beads.add_note(record.task_id, note)
    beads.add_labels(record.task_id, ["flow", "blocked", "blocker-result-missing"])
    beads.remove_labels(record.task_id, ["running", "reviewing"])
    record.status = "blocked"
    record.outcome = "missing-result"
    record.finished_at = _now()
    record.save(run_record_path(root, record.task_id))
    console.print(f"[yellow]Blocked[/yellow] {record.task_id}: worker exited without result.json")


def _missing_result_evidence(record: RunRecord) -> str:
    lines = [
        f"pid: {record.pid}",
        f"attempt: {record.attempt}",
        f"expected_result: {record.result}",
    ]
    last_message = _read_tail(Path(record.last_message), max_chars=4000)
    if last_message:
        lines.append(f"last_message:\n{last_message}")
    stderr = _read_tail(Path(record.prompt).parent / "stderr.log", max_chars=4000)
    if stderr:
        lines.append(f"stderr_tail:\n{stderr}")
    return "\n\n".join(lines)


def _read_tail(path: Path, *, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text.strip()
    return text[-max_chars:].strip()


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _load_worker_result(root: Path, task_id: str) -> WorkerResult:
    path = result_path(root, task_id)
    if not path.exists():
        record_path = run_record_path(root, task_id)
        if record_path.exists():
            record = RunRecord.load(record_path)
            path = Path(record.result)
    if not path.exists():
        raise ValueError(f"missing worker result: {path}")
    return WorkerResult.model_validate_json(path.read_text(encoding="utf-8"))


def _review_result(result: WorkerResult) -> None:
    if result.status != "completed":
        raise ValueError(f"task is not completed: {result.status}")
    failures = [check for check in result.verification if check.status == "failed"]
    if failures:
        names = ", ".join(check.command for check in failures)
        raise ValueError(f"verification failed: {names}")


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


def _run_record_paths(root: Path) -> list[tuple[Path, RunRecord]]:
    records = []
    for path in sorted((root / FLOW_DIR / "runs").glob("*/run.json")):
        records.append((path, RunRecord.load(path)))
    return records


def _with_labels(items: list[BeadSummary], labels: set[str]) -> list[BeadSummary]:
    return [item for item in items if labels.issubset(set(item.labels))]


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
