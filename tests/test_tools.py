from __future__ import annotations

import asyncio
import subprocess

import pytest

from agent_coder.tools.base import ToolFailure, WorkspacePaths
from agent_coder.tools.patch import apply_patch
from agent_coder.tools.registry import ToolRegistry
from agent_coder.tools.shell import command_risk


async def no_output(stream: str, text: str) -> None:
    del stream, text


def test_path_boundary_rejects_parent(tmp_path) -> None:
    paths = WorkspacePaths(tmp_path)
    with pytest.raises(ToolFailure, match="outside workspace"):
        paths.resolve("../outside.txt", for_creation=True)


def test_sensitive_paths_are_forbidden(tmp_path) -> None:
    paths = WorkspacePaths(tmp_path)
    with pytest.raises(ToolFailure, match="forbidden"):
        paths.resolve(".env", for_creation=True)
    assert paths.resolve(".env.example", for_creation=True) == tmp_path / ".env.example"
    with pytest.raises(ToolFailure, match="forbidden"):
        paths.resolve(".coding-agent/sessions/conv_secret.json")


def test_symlink_cannot_escape_workspace(tmp_path) -> None:
    outside = tmp_path.parent / "outside-agent-test"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    paths = WorkspacePaths(tmp_path)
    with pytest.raises(ToolFailure, match="outside workspace"):
        paths.resolve("escape/secret.txt", for_creation=True)


@pytest.mark.asyncio
async def test_read_write_search_and_list(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)

    write, _ = await registry.execute(
        "write_file",
        {"path": "src/main.py", "content": "value = 42\n", "createParents": True},
        on_output=no_output,
    )
    assert write.ok
    assert write.changed_files == ["src/main.py"]

    read, _ = await registry.execute(
        "read_file", {"path": "src/main.py"}, on_output=no_output
    )
    assert read.ok
    assert "1: value = 42" in read.data["content"]

    search, _ = await registry.execute(
        "search_text", {"query": "value", "path": "src"}, on_output=no_output
    )
    assert search.ok
    assert search.data["matchCount"] == 1

    listing, _ = await registry.execute(
        "list_directory", {"path": "src"}, on_output=no_output
    )
    assert listing.ok
    assert listing.data["entries"][0]["name"] == "main.py"


def test_apply_patch_updates_file(tmp_path) -> None:
    target = tmp_path / "example.py"
    target.write_text("answer = 41\nprint(answer)\n", encoding="utf-8")
    result = apply_patch(
        WorkspacePaths(tmp_path),
        {
            "patch": """*** Begin Patch
*** Update File: example.py
@@
-answer = 41
+answer = 42
 print(answer)
*** End Patch"""
        },
    )
    assert result.ok
    assert target.read_text(encoding="utf-8") == "answer = 42\nprint(answer)\n"
    assert "+answer = 42" in result.data["diff"]


def test_apply_patch_conflict_leaves_file_unchanged(tmp_path) -> None:
    target = tmp_path / "example.py"
    target.write_text("answer = 41\n", encoding="utf-8")
    with pytest.raises(ToolFailure, match="does not match"):
        apply_patch(
            WorkspacePaths(tmp_path),
            {
                "patch": """*** Begin Patch
*** Update File: example.py
@@
-missing = True
+missing = False
*** End Patch"""
            },
        )
    assert target.read_text(encoding="utf-8") == "answer = 41\n"


@pytest.mark.asyncio
async def test_run_command_captures_nonzero_exit(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)
    result, _ = await registry.execute(
        "run_command",
        {"command": "printf failure >&2; exit 7"},
        on_output=no_output,
    )
    assert not result.ok
    assert result.error_code == "command_failed"
    assert result.data["exitCode"] == 7
    assert "failure" in result.data["stderr"]


@pytest.mark.asyncio
async def test_run_command_rejects_absolute_path_outside_workspace(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)
    result, _ = await registry.execute(
        "run_command", {"command": "cat /etc/passwd"}, on_output=no_output
    )
    assert not result.ok
    assert result.error_code == "permission_denied"


@pytest.mark.asyncio
async def test_run_command_cannot_read_session_history(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)
    result, _ = await registry.execute(
        "run_command",
        {"command": "cat .coding-agent/sessions/conv_secret.json"},
        on_output=no_output,
    )
    assert not result.ok
    assert result.error_code == "permission_denied"


@pytest.mark.asyncio
async def test_tools_reject_unknown_arguments(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)
    result, _ = await registry.execute(
        "read_file", {"path": "x", "surprise": True}, on_output=no_output
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments"


def test_command_risk_is_conservative() -> None:
    assert command_risk("uv run pytest") == "low"
    assert command_risk("npm install") == "high"
    assert command_risk("git push origin main") == "forbidden"


@pytest.mark.asyncio
async def test_move_and_delete_tools_are_bounded(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)
    (tmp_path / "source.txt").write_text("content\n", encoding="utf-8")

    moved, _ = await registry.execute(
        "move_path",
        {"source": "source.txt", "destination": "nested/target.txt", "createParents": True},
        on_output=no_output,
    )
    assert moved.ok
    assert moved.changed_files == ["source.txt", "nested/target.txt"]
    assert (tmp_path / "nested" / "target.txt").is_file()

    deleted, _ = await registry.execute(
        "delete_path", {"path": "nested", "recursive": True}, on_output=no_output
    )
    assert deleted.ok
    assert not (tmp_path / "nested").exists()
    assert registry.risk("move_path", {}) == "medium"
    assert registry.risk("delete_path", {}) == "high"


@pytest.mark.asyncio
async def test_move_refuses_overwrite_and_delete_refuses_workspace_root(tmp_path) -> None:
    registry = ToolRegistry(tmp_path)
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    (tmp_path / "target.txt").write_text("target", encoding="utf-8")
    moved, _ = await registry.execute(
        "move_path",
        {"source": "source.txt", "destination": "target.txt"},
        on_output=no_output,
    )
    assert not moved.ok
    assert moved.error_code == "conflict"

    deleted, _ = await registry.execute(
        "delete_path", {"path": ".", "recursive": True}, on_output=no_output
    )
    assert not deleted.ok
    assert deleted.error_code == "permission_denied"
    assert (tmp_path / "source.txt").is_file()


@pytest.mark.asyncio
async def test_move_refuses_moving_directory_into_itself(tmp_path) -> None:
    (tmp_path / "source").mkdir()
    registry = ToolRegistry(tmp_path)

    result, _ = await registry.execute(
        "move_path",
        {"source": "source", "destination": "source/nested/moved", "createParents": True},
        on_output=no_output,
    )

    assert result.error_code == "invalid_arguments"
    assert not (tmp_path / "source" / "nested").exists()


@pytest.mark.asyncio
async def test_delete_unlinks_symlink_without_deleting_its_target(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    result, _ = await ToolRegistry(tmp_path).execute(
        "delete_path", {"path": "link.txt"}, on_output=no_output
    )
    assert result.ok
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_git_status_and_diff_return_structured_results(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "example.txt"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "example.txt"], cwd=tmp_path, check=True)
    target.write_text("before\nafter\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    status, _ = await registry.execute("git_status", {}, on_output=no_output)
    assert status.ok
    assert status.data["clean"] is False
    assert status.data["entries"][0]["path"] == "example.txt"

    worktree, _ = await registry.execute(
        "git_diff", {"scope": "worktree", "path": "example.txt"}, on_output=no_output
    )
    staged, _ = await registry.execute(
        "git_diff", {"scope": "staged", "path": "example.txt"}, on_output=no_output
    )
    assert worktree.ok and "+after" in worktree.data["diff"]
    assert staged.ok and "+before" in staged.data["diff"]


@pytest.mark.asyncio
async def test_git_tools_report_non_repository(tmp_path) -> None:
    result, _ = await ToolRegistry(tmp_path).execute("git_status", {}, on_output=no_output)
    assert not result.ok
    assert result.error_code == "not_git_repository"
