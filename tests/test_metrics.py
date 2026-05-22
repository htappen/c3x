from pathlib import Path

from c3x.metrics import collect_metrics
from c3x.paths import result_path, run_record_path
from c3x.schema import RunRecord, WorkerResult


def test_collect_metrics_counts_outcomes_and_attempts(tmp_path: Path) -> None:
    RunRecord(
        task_id="bd-1",
        branch="c3x/bd-1",
        worktree="wt",
        prompt="prompt",
        result=str(result_path(tmp_path, "bd-1")),
        last_message="last",
        status="landed",
        outcome="landed",
        attempt=1,
    ).save(run_record_path(tmp_path, "bd-1"))
    result_path(tmp_path, "bd-1").write_text(
        WorkerResult(
            task_id="bd-1",
            status="completed",
            task_kind="feature",
            attempt=1,
        ).model_dump_json(),
        encoding="utf-8",
    )

    data = collect_metrics(tmp_path)

    assert data["total_tasks"] == 1
    assert data["outcomes"]["landed"] == 1
    assert data["task_kinds"]["feature"] == 1
    assert data["avg_attempts_to_land"] == 1.0
