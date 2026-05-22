from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from c3x.config import FLOW_DIR
from c3x.schema import RunRecord, WorkerResult


def collect_metrics(root: Path) -> dict[str, Any]:
    records = _records(root)
    results = _results(root)
    attempts_by_task = Counter(record.task_id for record in records)
    status_counts = Counter(record.status for record in records)
    outcomes = Counter(record.outcome or record.status for record in records)
    task_kinds = Counter(result.task_kind or "unspecified" for result in results)
    blocker_categories = Counter(
        result.blocker_category or "unspecified"
        for result in results
        if result.status in {"blocked", "failed"}
    )
    rejected = [
        record.task_id
        for record in records
        if (record.outcome or record.status) in {"rejected", "blocked", "failed"}
    ]
    unfinished = [
        record.task_id
        for record in records
        if record.status in {"running", "blocked", "failed", "completed", "reviewed"}
    ]
    completed_attempts = [
        attempts_by_task[record.task_id]
        for record in records
        if record.status == "landed"
    ]
    avg_attempts = (
        sum(completed_attempts) / len(completed_attempts)
        if completed_attempts
        else 0.0
    )
    return {
        "total_runs": len(records),
        "total_tasks": len(attempts_by_task),
        "status_counts": dict(status_counts),
        "outcomes": dict(outcomes),
        "task_kinds": dict(task_kinds),
        "blocker_categories": dict(blocker_categories),
        "rejected_or_blocked": len(set(rejected)),
        "unfinished": len(set(unfinished)),
        "avg_attempts_to_land": round(avg_attempts, 2),
        "attempts_by_task": dict(attempts_by_task),
    }


def _records(root: Path) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in sorted((root / FLOW_DIR / "runs").glob("*/run.json")):
        records.append(RunRecord.load(path))
    return records


def _results(root: Path) -> list[WorkerResult]:
    results: list[WorkerResult] = []
    for path in sorted((root / FLOW_DIR / "runs").glob("*/result.json")):
        results.append(WorkerResult.model_validate_json(path.read_text(encoding="utf-8")))
    return results

