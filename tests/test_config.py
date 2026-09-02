from __future__ import annotations

import os

import pytest

from agent_coder.config import AgentConfig, ConfigurationError


def test_config_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW", "128000")
    config = AgentConfig.from_initialize({"workspaceRoot": str(tmp_path)})

    assert config.workspace_root == tmp_path.resolve()
    assert config.model == "test-model"
    assert config.api_key == "secret"
    assert config.context_window_tokens == 128000


def test_config_reads_workspace_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    monkeypatch.setenv("UNRELATED_EXISTING", "keep")
    (tmp_path / ".env").write_text(
        """OPENAI_API_KEY=dotenv-secret
OPENAI_BASE_URL=https://gateway.example/v1
AGENT_MODEL=dotenv-model
UNRELATED_SECRET=must-not-be-exported
AGENT_CONTEXT_WINDOW=64000
""",
        encoding="utf-8",
    )

    config = AgentConfig.from_initialize({"workspaceRoot": str(tmp_path)})

    assert config.api_key == "dotenv-secret"
    assert config.base_url == "https://gateway.example/v1"
    assert config.model == "dotenv-model"
    assert config.context_window_tokens == 64000
    assert "UNRELATED_SECRET" not in os.environ


def test_environment_and_cli_override_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".env").write_text(
        """OPENAI_API_KEY=file-key
OPENAI_BASE_URL=https://file.example/v1
AGENT_MODEL=file-model
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("AGENT_MODEL", "environment-model")

    config = AgentConfig.from_initialize(
        {
            "workspaceRoot": str(tmp_path),
            "model": "cli-model",
            "baseUrl": "https://cli.example/v1",
        }
    )

    assert config.api_key == "environment-key"
    assert config.base_url == "https://cli.example/v1"
    assert config.model == "cli-model"


def test_dotenv_symlink_cannot_escape_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    outside = tmp_path.parent / "outside.env"
    outside.write_text("OPENAI_API_KEY=secret\nAGENT_MODEL=test\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to(outside)

    with pytest.raises(ConfigurationError, match="outside the workspace"):
        AgentConfig.from_initialize({"workspaceRoot": str(tmp_path)})


def test_config_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        AgentConfig.from_initialize({"workspaceRoot": str(tmp_path), "model": "test"})


def test_context_window_defaults_to_128k(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)

    config = AgentConfig.from_initialize({"workspaceRoot": str(tmp_path), "model": "test"})

    assert config.context_window_tokens == 128000


def test_config_rejects_relative_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    with pytest.raises(ConfigurationError, match="absolute"):
        AgentConfig.from_initialize({"workspaceRoot": ".", "model": "test"})


def test_initialize_context_window_overrides_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW", "64000")

    config = AgentConfig.from_initialize(
        {
            "workspaceRoot": str(tmp_path),
            "model": "test",
            "options": {"contextWindowTokens": 200000},
        }
    )

    assert config.context_window_tokens == 200000


def test_context_chars_derived_from_token_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    config = AgentConfig.from_initialize(
        {"workspaceRoot": str(tmp_path), "model": "test"}
    )

    assert config.context_window_tokens == 128000
    assert config.max_context_chars == 128000 * 4


def test_context_chars_follows_context_window_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    config = AgentConfig.from_initialize(
        {
            "workspaceRoot": str(tmp_path),
            "model": "test",
            "options": {"contextWindowTokens": 32000},
        }
    )

    assert config.max_context_chars == 32000 * 4


def test_explicit_max_context_chars_overrides_derivation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    config = AgentConfig.from_initialize(
        {
            "workspaceRoot": str(tmp_path),
            "model": "test",
            "options": {"maxContextChars": 100_000},
        }
    )

    assert config.max_context_chars == 100_000


def test_context_chars_derivation_clamped_to_bounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    large = AgentConfig.from_initialize(
        {
            "workspaceRoot": str(tmp_path),
            "model": "test",
            "options": {"contextWindowTokens": 10_000_000},
        }
    )
    assert large.max_context_chars == 2_000_000

    small = AgentConfig.from_initialize(
        {
            "workspaceRoot": str(tmp_path),
            "model": "test",
            "options": {"contextWindowTokens": 1_024},
        }
    )
    assert small.max_context_chars == 20_000
