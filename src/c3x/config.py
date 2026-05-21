from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


FLOW_DIR = ".flow"
CONFIG_PATH = Path(FLOW_DIR) / "config.yml"


class AgentConfig(BaseModel):
    codex_command: str = "codex"


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

