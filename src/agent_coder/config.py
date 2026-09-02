from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


_MODEL_CONFIG_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "AGENT_MODEL",
    "AGENT_CONTEXT_WINDOW",
}


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
    context_window_tokens: int | None = None

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

        workspace = workspace.resolve()
        file_values = cls._dotenv_values(workspace)

        api_key = cls._first_nonempty(
            os.environ.get("OPENAI_API_KEY"), file_values.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required")

        model = cls._first_nonempty(
            payload.get("model"), os.environ.get("AGENT_MODEL"), file_values.get("AGENT_MODEL")
        )
        if not model:
            raise ConfigurationError("model or AGENT_MODEL is required")

        base_url = cls._first_nonempty(
            payload.get("baseUrl"),
            os.environ.get("OPENAI_BASE_URL"),
            file_values.get("OPENAI_BASE_URL"),
            "https://api.openai.com/v1",
        )
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
        context_window_tokens = cls._optional_bounded_int(
            options.get("contextWindowTokens"),
            os.environ.get("AGENT_CONTEXT_WINDOW"),
            file_values.get("AGENT_CONTEXT_WINDOW"),
            key="contextWindowTokens",
            minimum=1_024,
            maximum=10_000_000,
        )

        return cls(
            workspace_root=workspace,
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            max_steps=max_steps,
            command_timeout_ms=timeout,
            max_context_chars=context_chars,
            context_window_tokens=context_window_tokens,
        )

    @staticmethod
    def _first_nonempty(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _dotenv_values(workspace: Path) -> dict[str, str]:
        env_file = workspace / ".env"
        if not env_file.exists():
            return {}
        if not env_file.is_file():
            raise ConfigurationError("workspace .env must be a regular file")
        try:
            env_file.resolve().relative_to(workspace)
        except ValueError as exc:
            raise ConfigurationError("workspace .env must not point outside the workspace") from exc

        try:
            parsed = dotenv_values(env_file)
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"could not read workspace .env: {exc}") from exc
        return {
            key: value
            for key, value in parsed.items()
            if key in _MODEL_CONFIG_KEYS and isinstance(value, str)
        }

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

    @staticmethod
    def _optional_bounded_int(
        *values: Any,
        key: str,
        minimum: int,
        maximum: int,
    ) -> int | None:
        for value in values:
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                raise ConfigurationError(f"{key} must be an integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(f"{key} must be an integer") from exc
            if str(parsed) != str(value).strip():
                raise ConfigurationError(f"{key} must be an integer")
            if not minimum <= parsed <= maximum:
                raise ConfigurationError(f"{key} must be between {minimum} and {maximum}")
            return parsed
        return None
