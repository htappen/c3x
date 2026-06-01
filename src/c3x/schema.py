from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    task_kind: str | None = None
    attempt: int | None = None
    changed_files: list[str] = Field(default_factory=list)
    verification: list[VerificationCommand] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    blocker_category: str | None = None
    proposed_tasks: list[str] = Field(default_factory=list)
    scope_expansion: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] | None = None
    unfinished: list[str] = Field(default_factory=list)

    @field_validator("verification", mode="before")
    @classmethod
    def _normalize_verification(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        normalized: list[object] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"command": item, "status": "passed"})
            else:
                normalized.append(item)
        return normalized


class ReviewIssue(BaseModel):
    title: str
    description: str = ""
    severity: Literal["critical", "high", "medium", "low"] = "high"

    @field_validator("title", mode="after")
    @classmethod
    def _title_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review issue title cannot be empty")
        return value.strip()


class RequirementReview(BaseModel):
    requirement: str
    status: Literal["met", "unmet", "unclear"]
    evidence: str = ""


class ReviewResult(BaseModel):
    task_id: str
    status: Literal["approved", "blocked"]
    summary: str = ""
    requirements: list[RequirementReview] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)

    @field_validator("issues", mode="before")
    @classmethod
    def _normalize_issues(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        normalized: list[object] = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"title": item, "description": item, "severity": "high"})
            else:
                normalized.append(item)
        return normalized


class RunRecord(BaseModel):
    task_id: str
    branch: str
    worktree: str
    prompt: str
    result: str
    last_message: str
    provider: str = "codex"
    task_type: str = "worker"
    status: RunStatus = "running"
    pid: int | None = None
    attempt: int = 1
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    outcome: str | None = None

    @classmethod
    def load(cls, path: Path) -> "RunRecord":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
