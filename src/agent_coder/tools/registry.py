from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .base import Risk, ToolFailure, ToolResult, WorkspacePaths, require_object
from .filesystem import delete_path, list_directory, move_path, read_file, search_text, write_file
from .git import git_diff, git_status
from .patch import apply_patch, patch_contains_delete
from .shell import OutputCallback, command_risk, run_command


# Read-only tools have no side effects and may run concurrently within one reply.
READ_ONLY_TOOLS = frozenset(
    {"list_directory", "read_file", "search_text", "git_status", "git_diff"}
)
# Read-only tools whose handlers are synchronous and must be offloaded to a thread.
SYNC_READ_TOOLS = frozenset({"list_directory", "read_file", "search_text"})


class ToolRegistry:
    def __init__(self, workspace_root: Path, *, command_timeout_ms: int = 30_000) -> None:
        self.paths = WorkspacePaths(workspace_root)
        self.command_timeout_ms = command_timeout_ms

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [
            _function(
                "list_directory",
                "List direct children of a directory inside the workspace.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                        "includeHidden": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
            ),
            _function(
                "read_file",
                "Read a UTF-8 text file with line numbers. Use line ranges for large files.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "startLine": {"type": "integer", "minimum": 1},
                        "endLine": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "search_text",
                "Search file contents or file names inside the workspace.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "default": "."},
                        "mode": {"type": "string", "enum": ["content", "files"]},
                        "isRegex": {"type": "boolean", "default": False},
                        "caseSensitive": {"type": "boolean", "default": False},
                        "glob": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "write_file",
                "Create or fully replace a UTF-8 text file. Prefer apply_patch for local edits.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "expectedHash": {"type": "string"},
                        "createParents": {"type": "boolean", "default": False},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "apply_patch",
                "Apply an explicit Begin Patch/End Patch update to workspace text files.",
                {
                    "type": "object",
                    "properties": {"patch": {"type": "string", "minLength": 1}},
                    "required": ["patch"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "run_command",
                "Run a non-interactive shell command inside the workspace and return output.",
                {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "minLength": 1},
                        "cwd": {"type": "string", "default": "."},
                        "timeoutMs": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 120000,
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "git_status",
                "Read structured Git working-tree status without using a shell.",
                {
                    "type": "object",
                    "properties": {"includeUntracked": {"type": "boolean", "default": True}},
                    "additionalProperties": False,
                },
            ),
            _function(
                "git_diff",
                "Read a bounded Git diff for the workspace or one path.",
                {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["worktree", "staged", "all"]},
                        "path": {"type": "string"},
                        "contextLines": {"type": "integer", "minimum": 0, "maximum": 20},
                        "maxChars": {"type": "integer", "minimum": 1000, "maximum": 120000},
                    },
                    "additionalProperties": False,
                },
            ),
            _function(
                "move_path",
                "Move or rename a file or directory inside the workspace without overwriting.",
                {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                        "createParents": {"type": "boolean", "default": False},
                    },
                    "required": ["source", "destination"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "delete_path",
                "Delete one file, symlink, empty directory, or recursively delete a directory after approval.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "recursive": {"type": "boolean", "default": False},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            _function(
                "request_user_input",
                "Pause and ask the user one necessary, concise question before continuing.",
                {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "minLength": 1, "maxLength": 2000}
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            ),
        ]

    def risk(self, name: str, arguments: dict[str, Any]) -> Risk:
        if name in {"list_directory", "read_file", "search_text", "git_status", "git_diff", "request_user_input"}:
            return "low"
        if name == "write_file":
            path = str(arguments.get("path", "")).lower()
            if path.endswith(("package-lock.json", "uv.lock", "pyproject.toml", "package.json")):
                return "high"
            return "medium"
        if name == "apply_patch":
            return "high" if patch_contains_delete(arguments) else "medium"
        if name == "run_command":
            command = arguments.get("command")
            return command_risk(command) if isinstance(command, str) else "high"
        if name == "move_path":
            return "medium"
        if name == "delete_path":
            return "high"
        return "forbidden"

    async def execute(
        self,
        name: str,
        arguments: Any,
        *,
        on_output: OutputCallback,
    ) -> tuple[ToolResult, int]:
        started = time.monotonic()
        try:
            values = require_object(arguments)
            self._validate_keys(name, values)
            if name == "run_command":
                result = await run_command(
                    self.paths,
                    values,
                    default_timeout_ms=self.command_timeout_ms,
                    on_output=on_output,
                )
            elif name == "git_status":
                result = await git_status(self.paths, values)
            elif name == "git_diff":
                result = await git_diff(self.paths, values)
            else:
                result = self._dispatch_sync(name, values)
        except Exception as exc:
            result = self._error_result(exc)
        duration_ms = int((time.monotonic() - started) * 1000)
        return result, duration_ms

    def execute_sync(self, name: str, arguments: Any) -> tuple[ToolResult, int]:
        started = time.monotonic()
        try:
            values = require_object(arguments)
            self._validate_keys(name, values)
            result = self._dispatch_sync(name, values)
        except Exception as exc:
            result = self._error_result(exc)
        duration_ms = int((time.monotonic() - started) * 1000)
        return result, duration_ms

    def _dispatch_sync(self, name: str, values: dict[str, Any]) -> ToolResult:
        if name == "list_directory":
            return list_directory(self.paths, values)
        if name == "read_file":
            return read_file(self.paths, values)
        if name == "search_text":
            return search_text(self.paths, values)
        if name == "write_file":
            return write_file(self.paths, values)
        if name == "apply_patch":
            return apply_patch(self.paths, values)
        if name == "move_path":
            return move_path(self.paths, values)
        if name == "delete_path":
            return delete_path(self.paths, values)
        raise ToolFailure("unknown_tool", f"unknown tool: {name}")

    @staticmethod
    def _error_result(exc: Exception) -> ToolResult:
        if isinstance(exc, ToolFailure):
            return ToolResult(
                ok=False,
                summary=exc.message,
                error_code=exc.code,
                error_message=exc.message,
                retryable=exc.retryable,
            )
        if isinstance(exc, PermissionError):
            return ToolResult(
                ok=False,
                summary="operating system denied access",
                error_code="permission_denied",
                error_message=str(exc),
            )
        return ToolResult(
            ok=False,
            summary="unexpected tool error",
            error_code="internal_error",
            error_message=f"{type(exc).__name__}: {exc}",
        )

    @staticmethod
    def _validate_keys(name: str, arguments: dict[str, Any]) -> None:
        allowed = {
            "list_directory": {"path", "limit", "includeHidden"},
            "read_file": {"path", "startLine", "endLine"},
            "search_text": {
                "query",
                "path",
                "mode",
                "isRegex",
                "caseSensitive",
                "glob",
                "limit",
            },
            "write_file": {"path", "content", "expectedHash", "createParents"},
            "apply_patch": {"patch"},
            "run_command": {"command", "cwd", "timeoutMs"},
            "git_status": {"includeUntracked"},
            "git_diff": {"scope", "path", "contextLines", "maxChars"},
            "move_path": {"source", "destination", "createParents"},
            "delete_path": {"path", "recursive"},
            "request_user_input": {"question"},
        }.get(name)
        if allowed is None:
            raise ToolFailure("unknown_tool", f"unknown tool: {name}")
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            raise ToolFailure(
                "invalid_arguments", f"unexpected argument(s): {', '.join(unexpected)}"
            )


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


__all__ = ["ToolRegistry", "ToolResult", "READ_ONLY_TOOLS", "SYNC_READ_TOOLS"]
