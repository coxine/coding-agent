from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when the core cannot start with the supplied configuration."""


@dataclass(frozen=True, slots=True)
class AgentConfig:
    workspace_root: Path
    api_key: str
    base_url: str
    model: str
    max_steps: int = 30
    command_timeout_ms: int = 30_000
    max_context_chars: int = 200_000

    @classmethod
    def from_initialize(cls, payload: dict[str, Any]) -> "AgentConfig":
        raw_root = payload.get("workspaceRoot")
        if not isinstance(raw_root, str) or not raw_root:
            raise ConfigurationError("workspaceRoot must be a non-empty absolute path")

        workspace = Path(raw_root).expanduser()
        if not workspace.is_absolute():
            raise ConfigurationError("workspaceRoot must be an absolute path")
        if not workspace.exists() or not workspace.is_dir():
            raise ConfigurationError("workspaceRoot must be an existing directory")

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required")

        model_value = payload.get("model") or os.environ.get("AGENT_MODEL", "")
        model = model_value.strip() if isinstance(model_value, str) else ""
        if not model:
            raise ConfigurationError("model or AGENT_MODEL is required")

        base_value = payload.get("baseUrl") or os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        base_url = base_value.strip() if isinstance(base_value, str) else ""
        if not base_url.startswith(("http://", "https://")):
            raise ConfigurationError("baseUrl must start with http:// or https://")

        options = payload.get("options") or {}
        if not isinstance(options, dict):
            raise ConfigurationError("options must be an object")

        max_steps = cls._bounded_int(options, "maxSteps", 30, 1, 100)
        timeout = cls._bounded_int(options, "commandTimeoutMs", 30_000, 1_000, 120_000)
        context_chars = cls._bounded_int(
            options, "maxContextChars", 200_000, 20_000, 2_000_000
        )

        return cls(
            workspace_root=workspace.resolve(),
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            max_steps=max_steps,
            command_timeout_ms=timeout,
            max_context_chars=context_chars,
        )

    @staticmethod
    def _bounded_int(
        values: dict[str, Any], key: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigurationError(f"{key} must be an integer")
        if not minimum <= value <= maximum:
            raise ConfigurationError(f"{key} must be between {minimum} and {maximum}")
        return value

