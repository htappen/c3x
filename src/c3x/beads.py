from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BeadsError(RuntimeError):
    """Raised when a bd command fails or returns unusable output."""


@dataclass(frozen=True)
class BeadSummary:
    id: str
    title: str
    status: str | None = None
    priority: int | None = None
    type: str | None = None
    description: str | None = None
    notes: str | None = None
    acceptance: str | None = None
    labels: tuple[str, ...] = ()


class Beads:
    def __init__(self, root: Path, executable: str = "bd") -> None:
        self.root = root
        self.executable = executable
        self._summary_cache: dict[str, list[BeadSummary]] = {}

    def require_installed(self) -> None:
        if shutil.which(self.executable) is None:
            raise BeadsError(
                f"`{self.executable}` is not installed or is not on PATH. "
                "Run `scripts/setup.sh` or install Beads, then retry."
            )

    def init(self) -> None:
        self.require_installed()
        self._run(["init", "--quiet"], expect_json=False)
        self._invalidate_cache()

    def create_inbox_item(
        self,
        title: str,
        *,
        description: str | None = None,
        priority: int = 2,
    ) -> dict[str, Any]:
        args = [
            "create",
            title,
            "-t",
            "task",
            "-p",
            str(priority),
            "-l",
            "flow,inbox,idea,unreviewed,human-feedback",
        ]
        if description:
            args.extend(["--description", description])
        args.append("--json")
        created = self._run_json(args)
        self._invalidate_cache()
        return created

    def list_open(self) -> list[BeadSummary]:
        return self._cached_summaries("open", ["list", "--status", "open", "--limit", "0", "--json"])

    def list_active(self) -> list[BeadSummary]:
        return self._cached_summaries(
            "active",
            ["list", "--status", "open,in_progress,blocked", "--limit", "0", "--json"],
        )

    def list_closed(self) -> list[BeadSummary]:
        return self._cached_summaries("closed", ["list", "--status", "closed", "--limit", "0", "--json"])

    def list_active_export(self) -> list[BeadSummary]:
        return [
            item
            for item in self._export_summaries()
            if item.status in {"open", "in_progress", "blocked"} or item.status is None
        ]

    def ready(self) -> list[BeadSummary]:
        return self._cached_summaries("ready", ["ready", "--json"])

    def dependencies(self, task_id: str, *, direction: str = "down", dep_type: str = "blocks") -> list[dict[str, Any]]:
        args = ["dep", "list", task_id, "--direction", direction, "--type", dep_type, "--json"]
        payload = self._run_json(args)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("dependencies", "deps", "items", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def show(self, task_id: str) -> BeadSummary:
        payload = self._run_json(["show", task_id, "--json"])
        summaries = _summaries(payload)
        if not summaries and isinstance(payload, dict):
            summaries = _summaries([payload])
        if not summaries:
            raise BeadsError(f"Bead not found: {task_id}")
        return summaries[0]

    def add_note(self, task_id: str, note: str) -> None:
        self._run(["note", task_id, note], expect_json=False)
        self._invalidate_cache()

    def set_status(self, task_id: str, status: str) -> None:
        self._run(["update", task_id, "--status", status], expect_json=False)
        self._invalidate_cache()

    def add_labels(self, task_id: str, labels: list[str]) -> None:
        if not labels:
            return
        args = ["update", task_id]
        for label in labels:
            args.extend(["--add-label", label])
        self._run(args, expect_json=False)
        self._invalidate_cache()

    def remove_labels(self, task_id: str, labels: list[str]) -> None:
        if not labels:
            return
        args = ["update", task_id]
        for label in labels:
            args.extend(["--remove-label", label])
        self._run(args, expect_json=False)
        self._invalidate_cache()

    def close(self, task_id: str, reason: str) -> None:
        self._run(["close", task_id, "--reason", reason], expect_json=False)
        self._invalidate_cache()

    def compact_issue(self, task_id: str, summary: str, *, issue: BeadSummary | None = None) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=True) as summary_file:
            summary_file.write(summary)
            summary_file.flush()
            try:
                self._run(
                    ["admin", "compact", "--apply", "--id", task_id, "--summary", summary_file.name, "--force"],
                    expect_json=False,
                )
                self._invalidate_cache()
                return
            except BeadsError as exc:
                if "not yet supported in embedded mode" not in str(exc):
                    raise
        self._import_compacted_issue(task_id, summary, issue=issue)

    def _import_compacted_issue(self, task_id: str, summary: str, *, issue: BeadSummary | None) -> None:
        issue = issue or self.show(task_id)
        payload = {
            "id": task_id,
            "title": issue.title,
            "description": summary,
            "notes": "c3x compacted oversized notes into the issue description summary.",
            "status": issue.status or "open",
            "issue_type": issue.type or "task",
            "priority": issue.priority if issue.priority is not None else 2,
            "labels": list(issue.labels),
        }
        if issue.acceptance:
            payload["acceptance_criteria"] = issue.acceptance
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=True) as import_file:
            import_file.write(json.dumps(payload) + "\n")
            import_file.flush()
            self._run(["import", import_file.name], expect_json=False)
        self._invalidate_cache()

    def create_task(
        self,
        title: str,
        *,
        description: str,
        labels: list[str],
        issue_type: str = "task",
        priority: int = 2,
    ) -> dict[str, Any]:
        args = [
            "create",
            title,
            "--description",
            description,
            "--type",
            issue_type,
            "--priority",
            str(priority),
            "--labels",
            ",".join(labels),
            "--json",
        ]
        created = self._run_json(args)
        self._invalidate_cache()
        return created

    def add_blocker(self, blocker_id: str, blocked_id: str) -> None:
        self._run(["dep", blocker_id, "--blocks", blocked_id], expect_json=False)
        self._invalidate_cache()

    def remove_blocker(self, blocker_id: str, blocked_id: str) -> None:
        self._run(["dep", "remove", blocked_id, blocker_id], expect_json=False)
        self._invalidate_cache()

    def _cached_summaries(self, key: str, args: list[str]) -> list[BeadSummary]:
        cached = self._summary_cache.get(key)
        if cached is None:
            cached = _summaries(self._run_json(args))
            self._summary_cache[key] = cached
        return list(cached)

    def _export_summaries(self) -> list[BeadSummary]:
        cached = self._summary_cache.get("export")
        if cached is not None:
            return list(cached)
        path = self.root / ".beads" / "issues.jsonl"
        if not path.exists():
            return []
        summaries: list[BeadSummary] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            summaries.extend(_summaries([payload]))
        self._summary_cache["export"] = summaries
        return list(summaries)

    def _invalidate_cache(self) -> None:
        self._summary_cache.clear()

    def _run_json(self, args: list[str]) -> Any:
        result = self._run(args, expect_json=True)
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError as exc:
            raise BeadsError(f"`bd {' '.join(args)}` returned invalid JSON") from exc

    def _run(self, args: list[str], *, expect_json: bool) -> subprocess.CompletedProcess[str]:
        self.require_installed()
        result = subprocess.run(
            [self.executable, *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BeadsError(f"`bd {' '.join(args)}` failed: {detail}")
        if expect_json and not result.stdout.strip():
            raise BeadsError(f"`bd {' '.join(args)}` returned no JSON output")
        return result


def _summaries(payload: Any) -> list[BeadSummary]:
    items = _extract_items(payload)
    summaries: list[BeadSummary] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") or []
        summaries.append(
            BeadSummary(
                id=str(item.get("id") or ""),
                title=str(item.get("title") or ""),
                status=_optional_str(item.get("status")),
                priority=_optional_int(item.get("priority")),
                type=_optional_str(item.get("type") or item.get("issue_type")),
                description=_optional_str(item.get("description")),
                notes=_optional_str(item.get("notes")),
                acceptance=_optional_str(item.get("acceptance") or item.get("acceptance_criteria")),
                labels=tuple(str(label) for label in labels),
            )
        )
    return [summary for summary in summaries if summary.id]


def _extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("issues", "items", "results", "ready", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
