from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


FLOW_DIR = ".flow"
CONFIG_PATH = Path(FLOW_DIR) / "config.yml"

# Role names that appear inside a per-provider model block.
_ROLE_FIELDS = {"architect", "worker", "reviewer", "critic", "verify"}


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


class ProviderModelConfig(BaseModel):
    """Model names for each agent role, scoped to one provider."""

    architect: str = "gpt-5.4"
    worker: str = "gpt-5.4-mini"
    reviewer: str = "gpt-5.4"
    critic: str = "gpt-5.4"
    verify: str = "gpt-5.4"


_DEFAULT_PROVIDER_MODELS: dict[str, ProviderModelConfig] = {
    "codex": ProviderModelConfig(),
    "antigravity": ProviderModelConfig(
        architect="Gemini 3.5 Flash (Medium)",
        worker="Gemini 3.5 Flash (Medium)",
        reviewer="Gemini 3.5 Flash (Medium)",
        critic="Gemini 3.5 Flash (Medium)",
        verify="Gemini 3.5 Flash (Medium)",
    ),
}


class ModelConfig(BaseModel):
    """Per-provider model configuration.

    Keyed by provider name (e.g. ``"codex"``, ``"antigravity"``).  Access a provider's
    models with ``config.models["codex"]`` or the helper
    ``C3xConfig.models_for_provider(provider)``.
    """

    root: dict[str, ProviderModelConfig] = Field(
        default_factory=lambda: {k: v.model_copy() for k, v in _DEFAULT_PROVIDER_MODELS.items()}
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_dict(cls, value: Any) -> Any:
        """Accept a raw dict and normalise it into ``{"root": {...}}``.

        Handles two raw shapes:
        * New format: ``{"codex": {...}, "antigravity": {...}}``
        * Legacy flat format: ``{"architect": "...", "worker": "...", ...}``

        Empty dicts and non-dict values are passed through unchanged so that
        Pydantic's ``default_factory`` for ``root`` still applies.
        """
        if not isinstance(value, dict) or not value:
            return value
        # Already wrapped (e.g. constructed internally as ModelConfig(root=...)).
        if "root" in value and len(value) == 1:
            return value
        # Detect legacy flat format where keys are role names.
        if all(k in _ROLE_FIELDS for k in value):
            value = migrate_flat_models(value)
        # Migrate legacy "agy" key to "antigravity"
        if "agy" in value:
            value = dict(value)
            value["antigravity"] = value.pop("agy")
        return {"root": value}

    def __getitem__(self, provider: str) -> ProviderModelConfig:
        return self.root[provider]

    def get(self, provider: str, default: ProviderModelConfig | None = None) -> ProviderModelConfig | None:
        return self.root.get(provider, default)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        return {k: v.model_dump(**kwargs) for k, v in self.root.items()}


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

    def models_for_provider(self, provider: str) -> ProviderModelConfig:
        """Return the :class:`ProviderModelConfig` for *provider*.

        Falls back to the ``"codex"`` defaults when the provider is not
        explicitly configured.
        """
        return self.models.get(provider) or self.models.get("codex") or ProviderModelConfig()


def migrate_flat_models(flat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert a legacy flat ``models`` block to the new per-provider format.

    Old format::

        models:
          architect: gpt-5.4
          worker: gpt-5.4-mini

    New format::

        models:
          codex:
            architect: gpt-5.4
            worker: gpt-5.4-mini
          antigravity:
            architect: gpt-5.4
            worker: gpt-5.4-mini
    """
    provider_block = {k: flat[k] for k in _ROLE_FIELDS if k in flat}
    return {provider: dict(provider_block) for provider in _DEFAULT_PROVIDER_MODELS}


def migrate_config_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return *data* with any legacy sections migrated to current format.

    Currently handles the flat ``models`` block and the legacy ``agy`` key.
    The original dict is not mutated; a shallow copy is returned when a migration
    is applied.
    """
    if "models" in data and isinstance(data["models"], dict):
        models = data["models"]
        if models and all(k in _ROLE_FIELDS for k in models):
            data = dict(data)
            data["models"] = migrate_flat_models(models)
        elif "agy" in models:
            data = dict(data)
            models_copy = dict(models)
            models_copy["antigravity"] = models_copy.pop("agy")
            data["models"] = models_copy
    return data


def default_config() -> C3xConfig:
    return C3xConfig()


def load_config(root: Path) -> C3xConfig:
    path = root / CONFIG_PATH
    if not path.exists():
        return default_config()
    data = yaml.safe_load(path.read_text()) or {}
    data = migrate_config_data(data)
    return C3xConfig.model_validate(data)


def write_default_config(root: Path) -> Path:
    path = root / CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    data = default_config().model_dump(mode="json")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
