from __future__ import annotations

import subprocess

import pytest

import agent_coder.tools.filesystem as fs
from agent_coder.tools.base import WorkspacePaths
from agent_coder.tools.filesystem import search_text


def test_rg_exclude_args_cover_dependency_dirs() -> None:
    args = fs._rg_exclude_args()
    assert "!**/.venv/**" in args
    assert "!**/node_modules/**" in args
    assert "!**/dist/**" in args
    assert "!**/build/**" in args
    assert "!**/__pycache__/**" in args
    assert "!**/.pytest_cache/**" in args


def test_search_text_content_matches(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("value = 42\n", encoding="utf-8")
    paths = WorkspacePaths(tmp_path)

    result = search_text(paths, {"query": "value", "path": "src"})

    assert result.ok
    assert result.data["matchCount"] == 1
    match = result.data["matches"][0]
    assert match["path"] == "src/main.py"
    assert match["line"] == 1
    assert match["column"] == 1
    assert "value" in match["text"]


def test_search_text_files_mode(tmp_path) -> None:
    (tmp_path / "app_config.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("x\n", encoding="utf-8")
    paths = WorkspacePaths(tmp_path)

    result = search_text(paths, {"query": "config", "mode": "files"})

    assert result.ok
    assert result.data["matchCount"] == 1
    assert result.data["paths"] == ["app_config.py"]


@pytest.mark.skipif(not fs._has_rg(), reason="ripgrep is not available")
def test_search_text_respects_gitignore(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle\n", encoding="utf-8")
    paths = WorkspacePaths(tmp_path)

    result = search_text(paths, {"query": "needle", "path": "."})

    assert result.ok
    matched = [match["path"] for match in result.data["matches"]]
    assert "kept.txt" in matched
    assert "ignored.txt" not in matched


def test_search_text_falls_back_to_python_without_rg(tmp_path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(fs, "_has_rg", lambda: False)
    paths = WorkspacePaths(tmp_path)

    result = search_text(paths, {"query": "needle", "path": "src"})

    assert result.ok
    assert result.data["matchCount"] == 1
    assert result.data["matches"][0]["path"] == "src/main.py"
