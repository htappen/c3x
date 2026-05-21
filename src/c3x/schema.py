from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


RunStatus = Literal["running", "completed", "blocked", "failed", "reviewed", "landed"]


class VerificationCommand(BaseModel):
    command: str
    status: Literal["passed", "failed", "skipped"]
    exit_code: int | None = None
    log_path: str | None = None


class WorkerResult(BaseModel):
    task_id: str
    status: Literal["completed", "blocked", "failed"]
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    verification: list[VerificationCommand] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    proposed_tasks: list[str] = Field(default_factory=list)
    scope_expansion: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    task_id: str
    branch: str
    worktree: str
    prompt: str
    result: str
    last_message: str
    status: RunStatus = "running"
    pid: int | None = None

    @classmethod
    def load(cls, path: Path) -> "RunRecord":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

