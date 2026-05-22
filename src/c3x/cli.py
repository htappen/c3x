from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from c3x.agent import AgentError, start_worker
from c3x.beads import Beads, BeadsError, BeadSummary
from c3x.config import FLOW_DIR, load_config, write_default_config
from c3x.gitops import (
    GitError,
    commit_ledger_changes,
    current_branch,
    delete_branch,
    merge_branch,
    remove_worktree,
)
from c3x.metrics import collect_metrics
from c3x.paths import pause_path, result_path, run_record_path
from c3x.schema import RunRecord, WorkerResult
from c3x.verify import run_verification


app = typer.Typer(
    name="c3x",
    help="Local agentic coding supervisor for Codex and Beads.",
    no_args_is_help=True,
)
console = Console()


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
) -> None:
    """Add raw feedback to the Beads-backed c3x inbox."""
    root = _root()
    try:
        item = _beads(root).create_inbox_item(title, description=description, priority=priority)
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
def status() -> None:
    """Show the current c3x project status."""
    root = _root()
    config = load_config(root)
    try:
        open_items = _beads(root).list_active()
        ready_items = _beads(root).ready()
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc

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
    console.print(table)


@app.command()
def answer(
    task_id: Annotated[str, typer.Argument(help="Question or task id to answer.")],
    text: Annotated[str, typer.Argument(help="Answer text to append to the bead.")],
) -> None:
    """Record a human answer on a question bead."""
    root = _root()
    beads = _beads(root)
    try:
        beads.add_note(task_id, f"Human answer: {text}")
        beads.add_labels(task_id, ["answered"])
        beads.remove_labels(task_id, ["question"])
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Answered[/green] {task_id}")


@app.command()
def run(
    once: Annotated[bool, typer.Option("--once", help="Run one supervisor tick and exit.")] = False,
    dispatch: Annotated[bool, typer.Option("--dispatch", help="Start ready tasks after planning.")] = False,
    interval: Annotated[int, typer.Option("--interval", min=1, help="Loop sleep seconds.")] = 5,
) -> None:
    """Run the c3x supervisor loop."""
    while True:
        if pause_path(_root()).exists():
            console.print("[yellow]c3x is paused.[/yellow]")
            if once:
                return
            time.sleep(interval)
            continue
        _supervisor_tick(_root(), dispatch=dispatch)
        if once:
            return
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
) -> None:
    """Merge a reviewed task branch and close the bead."""
    root = _root()
    _warn_if_risky_flow_branch(root)
    try:
        record = RunRecord.load(run_record_path(root, task_id))
        if record.status != "reviewed":
            raise ValueError(f"{task_id} is not reviewed")
        merge_branch(root, record.branch)
        beads = _beads(root)
        beads.close(task_id, "Landed by c3x")
        beads.add_labels(task_id, ["landed"])
        commit_ledger_changes(root, f"Close c3x task {task_id}")
        record.status = "landed"
        record.outcome = "landed"
        record.finished_at = _now()
        record.save(run_record_path(root, task_id))
    except (BeadsError, GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Landed[/green] {task_id}")


@app.command()
def cleanup(
    task_id: Annotated[str, typer.Argument(help="Landed task id to clean up.")],
) -> None:
    """Remove a landed task worktree and branch."""
    root = _root()
    try:
        record = RunRecord.load(run_record_path(root, task_id))
        if record.status != "landed":
            raise ValueError(f"{task_id} is not landed")
        remove_worktree(root, Path(record.worktree))
        delete_branch(root, record.branch)
    except (GitError, ValueError) as exc:
        raise typer.Exit(_error(str(exc))) from exc
    console.print(f"[green]Cleaned[/green] {task_id}")


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


def _supervisor_tick(root: Path, *, dispatch: bool) -> None:
    beads = _beads(root)
    _import_finished_results(root, beads)
    _plan_inbox(root, beads)
    _critic_tick(beads)
    if dispatch:
        _warn_if_risky_flow_branch(root)
        config = load_config(root)
        running = len(_with_labels(beads.list_active(), {"flow", "running"}))
        slots = max(config.limits.max_parallel_workers - running, 0)
        for task in beads.ready()[:slots]:
            if "flow" in task.labels:
                start_worker(root, config, task)
                beads.set_status(task.id, "in_progress")
                beads.add_labels(task.id, ["flow", "running"])
                beads.remove_labels(task.id, ["ready", "blocked", "reviewing"])
    status()


def _plan_inbox(root: Path, beads: Beads) -> None:
    inbox_items = _with_labels(beads.list_open(), {"flow", "inbox", "idea"})
    for item in inbox_items:
        if "planned" in item.labels:
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
        beads.close(item.id, f"Planned as {child_id}")
        console.print(f"[green]Planned[/green] {item.id} -> {child_id}")


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
            continue
        result = WorkerResult.model_validate_json(result_file.read_text(encoding="utf-8"))
        if result.task_id != record.task_id:
            beads.add_note(record.task_id, "Worker result rejected: task id mismatch")
            beads.add_labels(record.task_id, ["flow", "blocked", "rejected", "blocker-result-schema"])
            record.status = "blocked"
            record.outcome = "rejected"
        elif result.status == "completed":
            beads.add_note(record.task_id, _result_note(result))
            beads.add_labels(record.task_id, ["flow", "reviewing", "completed-by-agent"])
            beads.remove_labels(record.task_id, ["running", "blocked"])
            record.status = "completed"
            record.outcome = "completed"
        else:
            beads.add_note(record.task_id, _result_note(result))
            category = result.blocker_category or "unknown"
            beads.add_labels(record.task_id, ["flow", "blocked", f"blocker-{category}"])
            beads.remove_labels(record.task_id, ["running", "reviewing"])
            record.status = "blocked"
            record.outcome = result.status
        record.finished_at = _now()
        record.save(run_record_path(root, record.task_id))


def _load_worker_result(root: Path, task_id: str) -> WorkerResult:
    path = result_path(root, task_id)
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
    records = []
    for path in sorted((root / FLOW_DIR / "runs").glob("*/run.json")):
        records.append(RunRecord.load(path))
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
