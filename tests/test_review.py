from c3x.cli import _review_result
from c3x.schema import VerificationCommand, WorkerResult


def test_review_allows_completed_result() -> None:
    result = WorkerResult(
        task_id="bd-1",
        status="completed",
        verification=[VerificationCommand(command="pytest", status="passed", exit_code=0)],
    )

    _review_result(result)


def test_worker_result_accepts_string_verification_commands() -> None:
    result = WorkerResult.model_validate(
        {
            "task_id": "bd-1",
            "status": "completed",
            "verification": ["pytest"],
        }
    )

    assert result.verification[0].command == "pytest"
    assert result.verification[0].status == "passed"


def test_review_allows_failed_verification_for_dummy_step() -> None:
    result = WorkerResult(
        task_id="bd-1",
        status="completed",
        verification=[VerificationCommand(command="pytest", status="failed", exit_code=1)],
    )

    _review_result(result)
