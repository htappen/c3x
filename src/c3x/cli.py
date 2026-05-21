from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from c3x.beads import Beads, BeadsError, BeadSummary
from c3x.config import FLOW_DIR, load_config, write_default_config


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
        open_items = _beads(root).list_open()
        ready_items = _beads(root).ready()
    except BeadsError as exc:
        raise typer.Exit(_error(str(exc))) from exc

    inbox_items = [
        item
        for item in open_items
        if {"flow", "inbox", "idea"}.issubset(set(item.labels))
    ]

    table = Table(title="c3x status")
    table.add_column("Bucket")
    table.add_column("Count", justify="right")
    table.add_row("Inbox", str(len(inbox_items)))
    table.add_row("Open", str(len(open_items)))
    table.add_row("Ready", str(len(ready_items)))
    table.add_row("Max parallel workers", str(config.limits.max_parallel_workers))
    console.print(table)


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


def _error(message: str) -> int:
    console.print(f"[red]error:[/red] {message}")
    return 1


if __name__ == "__main__":
    app()

