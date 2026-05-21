from pathlib import Path

from c3x.verify import run_verification


def test_run_verification_records_pass_and_log(tmp_path: Path) -> None:
    results = run_verification(tmp_path, ["printf ok"])

    assert results[0].status == "passed"
    assert results[0].exit_code == 0
    assert (tmp_path / results[0].log_path).read_text() == "ok"


def test_run_verification_records_failure(tmp_path: Path) -> None:
    results = run_verification(tmp_path, ["exit 7"])

    assert results[0].status == "failed"
    assert results[0].exit_code == 7

