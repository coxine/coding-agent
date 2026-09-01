from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Risk = Literal["low", "medium", "high", "forbidden"]


@dataclass(slots=True)
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    truncated: bool = False
    changed_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "error": None
            if self.ok
            else {
                "code": self.error_code or "internal_error",
                "message": self.error_message or self.summary,
                "retryable": self.retryable,
            },
            "meta": {"truncated": self.truncated},
        }


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class WorkspacePaths:
    _forbidden_names = {
        ".git",
        ".coding-agent",
        "id_rsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "tokens.json",
        "secrets.json",
    }
    _forbidden_globs = ("*.pem", "*.key")

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, raw_path: str, *, for_creation: bool = False) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolFailure("invalid_arguments", "path must be a non-empty string")
        supplied = Path(raw_path).expanduser()
        candidate = supplied if supplied.is_absolute() else self.root / supplied

        if for_creation and not candidate.exists():
            existing = candidate.parent
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            resolved = existing.resolve() / candidate.relative_to(existing)
        else:
            resolved = candidate.resolve(strict=False)

        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise ToolFailure(
                "path_outside_workspace", f"path is outside workspace: {raw_path}"
            ) from exc

        self._check_forbidden(relative)
        return resolved

    def display(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix() or "."

    def _check_forbidden(self, relative: Path) -> None:
        parts = relative.parts
        for part in parts:
            lower = part.lower()
            if lower == ".env" or (lower.startswith(".env.") and lower != ".env.example"):
                raise ToolFailure("path_forbidden", f"access to {relative.as_posix()} is forbidden")
            if lower in self._forbidden_names:
                raise ToolFailure("path_forbidden", f"access to {relative.as_posix()} is forbidden")
            if any(fnmatch.fnmatch(lower, pattern) for pattern in self._forbidden_globs):
                raise ToolFailure("path_forbidden", f"access to {relative.as_posix()} is forbidden")


def require_object(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ToolFailure("invalid_arguments", "tool arguments must be a JSON object")
    return arguments


def require_string(arguments: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ToolFailure("invalid_arguments", f"{key} must be a string")
    return value


def optional_int(
    arguments: dict[str, Any], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolFailure(
            "invalid_arguments", f"{key} must be an integer between {minimum} and {maximum}"
        )
    return value
