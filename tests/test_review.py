import pytest

from c3x.cli import _review_result
from c3x.schema import VerificationCommand, WorkerResult


def test_review_allows_completed_result() -> None:
    result = WorkerResult(
        task_id="bd-1",
        status="completed",
        verification=[VerificationCommand(command="pytest", status="passed", exit_code=0)],
    )

    _review_result(result)


def test_review_blocks_failed_verification() -> None:
    result = WorkerResult(
        task_id="bd-1",
        status="completed",
        verification=[VerificationCommand(command="pytest", status="failed", exit_code=1)],
    )

    with pytest.raises(ValueError, match="verification failed"):
        _review_result(result)

