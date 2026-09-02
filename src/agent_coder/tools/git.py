from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import ToolFailure, ToolResult, WorkspacePaths, optional_int


async def _git(root: Path, *arguments: str) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ToolFailure("command_not_found", "git is not installed") from exc
    stdout, stderr = await process.communicate()
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace").strip(),
    )


def _git_failure(code: int, stderr: str) -> ToolFailure:
    message = stderr or f"git exited with code {code}"
    error_code = "not_git_repository" if "not a git repository" in message.lower() else "git_error"
    return ToolFailure(error_code, message, retryable=False)


async def git_status(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    include_untracked = arguments.get("includeUntracked", True)
    if not isinstance(include_untracked, bool):
        raise ToolFailure("invalid_arguments", "includeUntracked must be a boolean")
    untracked = "all" if include_untracked else "no"
    code, output, stderr = await _git(
        paths.root, "status", "--porcelain=v1", "-z", f"--untracked-files={untracked}"
    )
    if code != 0:
        raise _git_failure(code, stderr)

    records = output.split("\0")
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            continue
        index_status, worktree_status, path = record[0], record[1], record[3:]
        entry = {
            "path": path,
            "indexStatus": index_status,
            "workTreeStatus": worktree_status,
        }
        if index_status in {"R", "C"} or worktree_status in {"R", "C"}:
            if index < len(records) and records[index]:
                entry["originalPath"] = records[index]
                index += 1
        entries.append(entry)

    branch_code, branch, _ = await _git(paths.root, "branch", "--show-current")
    branch_name = branch.strip() if branch_code == 0 else ""
    return ToolResult(
        ok=True,
        summary=f"Git working tree has {len(entries)} changed path(s)",
        data={"branch": branch_name or None, "clean": not entries, "entries": entries},
    )


async def git_diff(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    scope = arguments.get("scope", "worktree")
    if scope not in {"worktree", "staged", "all"}:
        raise ToolFailure("invalid_arguments", "scope must be worktree, staged, or all")
    raw_path = arguments.get("path")
    if raw_path is not None and not isinstance(raw_path, str):
        raise ToolFailure("invalid_arguments", "path must be a string")
    context_lines = optional_int(arguments, "contextLines", 3, 0, 20)
    max_chars = optional_int(arguments, "maxChars", 60_000, 1_000, 120_000)
    path_argument: list[str] = []
    display_path: str | None = None
    if raw_path is not None:
        resolved = paths.resolve(raw_path, for_creation=True)
        display_path = paths.display(resolved)
        path_argument = ["--", display_path]

    parts: list[str] = []
    for current_scope in (["worktree", "staged"] if scope == "all" else [scope]):
        command = ["diff", "--no-ext-diff", "--no-color", f"--unified={context_lines}"]
        if current_scope == "staged":
            command.append("--cached")
        code, output, stderr = await _git(paths.root, *command, *path_argument)
        if code != 0:
            raise _git_failure(code, stderr)
        if output:
            if scope == "all":
                parts.append(f"--- {current_scope} ---\n{output}")
            else:
                parts.append(output)

    diff = "\n".join(parts)
    truncated = len(diff) > max_chars
    if truncated:
        half = max_chars // 2
        diff = diff[:half] + "\n... diff truncated ...\n" + diff[-half:]
    return ToolResult(
        ok=True,
        summary=f"Read {scope} git diff" + (f" for {display_path}" if display_path else ""),
        data={
            "scope": scope,
            "path": display_path,
            "diff": diff,
            "empty": not diff,
            "truncated": truncated,
        },
        truncated=truncated,
    )
