from __future__ import annotations

import pytest

from agent_coder.config import AgentConfig, ConfigurationError


def test_config_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    config = AgentConfig.from_initialize({"workspaceRoot": str(tmp_path)})

    assert config.workspace_root == tmp_path.resolve()
    assert config.model == "test-model"
    assert config.api_key == "secret"


def test_config_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        AgentConfig.from_initialize({"workspaceRoot": str(tmp_path), "model": "test"})


def test_config_rejects_relative_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    with pytest.raises(ConfigurationError, match="absolute"):
        AgentConfig.from_initialize({"workspaceRoot": ".", "model": "test"})

