from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


FLOW_DIR = ".flow"
CONFIG_PATH = Path(FLOW_DIR) / "config.yml"


class AgentConfig(BaseModel):
    provider: str = "codex"
    provider_overrides: dict[str, str] = Field(default_factory=dict)
    codex_command: str = "codex"
    codex_args: list[str] = Field(
        default_factory=lambda: [
            "exec",
            "--full-auto",
            "--model",
            "{model}",
            "--cd",
            "{worktree}",
            "--output-last-message",
            "{last_message}",
            "{prompt}",
        ]
    )
    codex_resume_args: list[str] = Field(
        default_factory=lambda: [
            "exec",
            "resume",
            "--model",
            "{model}",
            "--output-last-message",
            "{last_message}",
            "{session_id}",
            "{prompt}",
        ]
    )
    antigravity_command: str = "~/.local/bin/agy.va39"
    antigravity_args: list[str] = Field(
        default_factory=lambda: [
            "--dangerously-skip-permissions",
            "--sandbox",
            "--add-dir",
            "{worktree}",
            "--print",
            "{prompt_content}",
        ]
    )
    antigravity_resume_args: list[str] = Field(
        default_factory=lambda: [
            "--dangerously-skip-permissions",
            "--sandbox",
            "--add-dir",
            "{worktree}",
            "--conversation",
            "{session_id}",
            "--print",
            "{prompt_content}",
        ]
    )

    @field_validator("provider", mode="after")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        if value not in {"codex", "antigravity"}:
            raise ValueError("agents.provider must be 'codex' or 'antigravity'")
        return value

    @field_validator("provider_overrides", mode="after")
    @classmethod
    def _validate_provider_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        invalid = sorted({provider for provider in value.values() if provider not in {"codex", "antigravity"}})
        if invalid:
            raise ValueError("agents.provider_overrides values must be 'codex' or 'antigravity'")
        return value


class ModelConfig(BaseModel):
    architect: str = "gpt-5.4"
    worker: str = "gpt-5.4-mini"
    reviewer: str = "gpt-5.4"
    critic: str = "gpt-5.4"
    verify: str = "gpt-5.4"


class LimitConfig(BaseModel):
    max_parallel_workers: int = 3
    max_files_per_task: int = 8
    max_context_tokens_worker: int = 50_000
    max_runtime_minutes: int = 45
    require_clean_worktree: bool = True


class PermissionConfig(BaseModel):
    worker_shell: str = "sandboxed_full_auto"
    network: str = "false_by_default"
    merge: str = "reviewer_only"


class C3xConfig(BaseModel):
    agents: AgentConfig = Field(default_factory=AgentConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    limits: LimitConfig = Field(default_factory=LimitConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    verify: list[str] = Field(default_factory=list)


def default_config() -> C3xConfig:
    return C3xConfig()


def load_config(root: Path) -> C3xConfig:
    path = root / CONFIG_PATH
    if not path.exists():
        return default_config()
    data = yaml.safe_load(path.read_text()) or {}
    return C3xConfig.model_validate(data)


def write_default_config(root: Path) -> Path:
    path = root / CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    data = default_config().model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
