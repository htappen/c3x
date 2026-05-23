from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live
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
def status() -> None:
    """Show the current c3x project status."""
    root = _root()
    try:
        table = _build_status_table(root)
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc
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
    with Live(_build_status_table(root), console=console, refresh_per_second=4) as live:
        while True:
            if pause_path(root).exists():
                console.print("[yellow]c3x is paused.[/yellow]")
                live.update(_build_status_table(root))
                if once:
                    return
                time.sleep(interval)
                continue
            _supervisor_tick(root, dispatch=dispatch)
            live.update(_build_status_table(root))
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
) -> None:
    """Run the autonomous c3x watch loop."""
    root = _root()
    with Live(_build_status_table(root), console=console, refresh_per_second=4) as live:
        while True:
            if pause_path(root).exists():
                console.print("[yellow]c3x is paused.[/yellow]")
                live.update(_build_status_table(root))
                time.sleep(interval)
                continue
            _supervisor_tick(
                root,
                dispatch=True,
                review=review,
                land=land,
                cleanup_done=cleanup_done,
            )
            live.update(_build_status_table(root))
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


def _supervisor_tick(
    root: Path,
    *,
    dispatch: bool,
    review: bool = False,
    land: bool = False,
    cleanup_done: bool = False,
) -> None:
    beads = _beads(root)
    _import_finished_results(root, beads)
    _plan_inbox(root, beads)
    _critic_tick(beads)
    if dispatch:
        config = load_config(root)
        running = len(_with_labels(beads.list_active(), {"flow", "running"}))
        slots = max(config.limits.max_parallel_workers - running, 0)
        for task in beads.ready()[:slots]:
            if "flow" in task.labels:
                start_worker(root, config, task)
                beads.set_status(task.id, "in_progress")
                beads.add_labels(task.id, ["flow", "running"])
                beads.remove_labels(task.id, ["ready", "blocked", "reviewing"])
    if review:
        _auto_review(root, beads)
    if land:
        _auto_land(root, beads, cleanup_done=cleanup_done)


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
                remove_worktree(root, Path(record.worktree))
                delete_branch(root, record.branch)
                console.print(f"[green]Cleaned[/green] {item.id}")
        except (BeadsError, GitError, ValueError) as exc:
            beads.add_note(item.id, f"c3x auto-land blocked: {exc}")
            beads.add_labels(item.id, ["flow", "blocked", "land-blocked"])
            console.print(f"[yellow]Land blocked[/yellow] {item.id}: {exc}")


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
