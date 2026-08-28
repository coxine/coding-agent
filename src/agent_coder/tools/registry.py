from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .base import Risk, ToolFailure, ToolResult, WorkspacePaths, require_object
from .filesystem import list_directory, read_file, search_text, write_file
from .patch import apply_patch, patch_contains_delete
from .shell import OutputCallback, command_risk, run_command


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
        ]

    def risk(self, name: str, arguments: dict[str, Any]) -> Risk:
        if name in {"list_directory", "read_file", "search_text"}:
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
            if name == "list_directory":
                result = list_directory(self.paths, values)
            elif name == "read_file":
                result = read_file(self.paths, values)
            elif name == "search_text":
                result = search_text(self.paths, values)
            elif name == "write_file":
                result = write_file(self.paths, values)
            elif name == "apply_patch":
                result = apply_patch(self.paths, values)
            elif name == "run_command":
                result = await run_command(
                    self.paths,
                    values,
                    default_timeout_ms=self.command_timeout_ms,
                    on_output=on_output,
                )
            else:
                raise ToolFailure("unknown_tool", f"unknown tool: {name}")
        except ToolFailure as exc:
            result = ToolResult(
                ok=False,
                summary=exc.message,
                error_code=exc.code,
                error_message=exc.message,
                retryable=exc.retryable,
            )
        except PermissionError as exc:
            result = ToolResult(
                ok=False,
                summary="operating system denied access",
                error_code="permission_denied",
                error_message=str(exc),
            )
        except Exception as exc:  # Boundary: handlers never crash the agent loop.
            result = ToolResult(
                ok=False,
                summary="unexpected tool error",
                error_code="internal_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return result, duration_ms

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


__all__ = ["ToolRegistry", "ToolResult"]
