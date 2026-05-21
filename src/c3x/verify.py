from __future__ import annotations

import subprocess
from pathlib import Path

from c3x.schema import VerificationCommand


def run_verification(root: Path, commands: list[str]) -> list[VerificationCommand]:
    results: list[VerificationCommand] = []
    if not commands:
        return results
    logs_dir = root / ".flow" / "verify-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for index, command in enumerate(commands, start=1):
        log_path = logs_dir / f"{index}.log"
        result = subprocess.run(
            command,
            cwd=root,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log_path.write_text(result.stdout or "", encoding="utf-8")
        results.append(
            VerificationCommand(
                command=command,
                status="passed" if result.returncode == 0 else "failed",
                exit_code=result.returncode,
                log_path=str(log_path.relative_to(root)),
            )
        )
    return results

