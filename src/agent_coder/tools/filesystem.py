from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .base import ToolFailure, ToolResult, WorkspacePaths, optional_int, require_string


MAX_TEXT_CHARS = 40_000


def _hash_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _read_utf8(path: Path) -> tuple[str, bytes]:
    if not path.exists():
        raise ToolFailure("file_not_found", f"{path.name} does not exist", retryable=True)
    if not path.is_file():
        raise ToolFailure("not_a_file", f"{path.name} is not a regular file", retryable=True)
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ToolFailure("unsupported_file_type", f"{path.name} appears to be binary")
    try:
        return raw.decode("utf-8-sig"), raw
    except UnicodeDecodeError as exc:
        raise ToolFailure("decode_error", f"{path.name} is not valid UTF-8") from exc


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    existing_mode = path.stat().st_mode if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unified_diff(path: str, before: str, after: str) -> tuple[str, bool]:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}" if before else "/dev/null",
            tofile=f"b/{path}",
        )
    )
    if len(diff) <= 60_000:
        return diff, False
    return diff[:30_000] + "\n... diff truncated ...\n" + diff[-30_000:], True


def list_directory(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    raw_path = arguments.get("path", ".")
    if not isinstance(raw_path, str):
        raise ToolFailure("invalid_arguments", "path must be a string")
    limit = optional_int(arguments, "limit", 200, 1, 500)
    include_hidden = arguments.get("includeHidden", False)
    if not isinstance(include_hidden, bool):
        raise ToolFailure("invalid_arguments", "includeHidden must be a boolean")

    directory = paths.resolve(raw_path)
    if not directory.exists():
        raise ToolFailure("file_not_found", f"directory does not exist: {raw_path}", retryable=True)
    if not directory.is_dir():
        raise ToolFailure("not_a_directory", f"not a directory: {raw_path}", retryable=True)

    entries: list[dict[str, Any]] = []
    visible = []
    for entry in directory.iterdir():
        if not include_hidden and entry.name.startswith(".") and entry.name != ".gitignore":
            continue
        visible.append(entry)
    visible.sort(key=lambda item: (not item.is_dir(), item.name.casefold()))

    for entry in visible[:limit]:
        item: dict[str, Any] = {
            "name": entry.name,
            "path": paths.display(entry),
            "type": "directory" if entry.is_dir() else "file" if entry.is_file() else "other",
        }
        if entry.is_file():
            item["size"] = entry.stat().st_size
        entries.append(item)

    display = paths.display(directory)
    truncated = len(visible) > limit
    return ToolResult(
        ok=True,
        summary=f"Listed {len(entries)} entries in {display}",
        data={
            "path": display,
            "entries": entries,
            "totalEntries": len(visible),
            "truncated": truncated,
        },
        truncated=truncated,
    )


def read_file(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    raw_path = require_string(arguments, "path")
    start_line = optional_int(arguments, "startLine", 1, 1, 10_000_000)
    end_value = arguments.get("endLine")
    if end_value is None:
        end_line = start_line + 399
    elif isinstance(end_value, bool) or not isinstance(end_value, int) or end_value < start_line:
        raise ToolFailure("invalid_arguments", "endLine must be an integer >= startLine")
    else:
        end_line = min(end_value, start_line + 399)

    path = paths.resolve(raw_path)
    text, raw = _read_utf8(path)
    lines = text.splitlines()
    selected = lines[start_line - 1 : end_line]
    numbered = "\n".join(
        f"{number}: {line}" for number, line in enumerate(selected, start=start_line)
    )
    truncated = len(numbered) > MAX_TEXT_CHARS or end_line < len(lines)
    if len(numbered) > MAX_TEXT_CHARS:
        numbered = numbered[:MAX_TEXT_CHARS] + "\n... content truncated ..."

    actual_end = start_line + len(selected) - 1 if selected else start_line - 1
    display = paths.display(path)
    return ToolResult(
        ok=True,
        summary=f"Read lines {start_line}-{actual_end} from {display}",
        data={
            "path": display,
            "startLine": start_line,
            "endLine": actual_end,
            "totalLines": len(lines),
            "content": numbered,
            "truncated": truncated,
            "contentHash": _hash_bytes(raw),
        },
        truncated=truncated,
    )


def search_text(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    import re

    query = require_string(arguments, "query")
    raw_path = arguments.get("path", ".")
    if not isinstance(raw_path, str):
        raise ToolFailure("invalid_arguments", "path must be a string")
    mode = arguments.get("mode", "content")
    if mode not in {"content", "files"}:
        raise ToolFailure("invalid_arguments", "mode must be content or files")
    is_regex = arguments.get("isRegex", False)
    case_sensitive = arguments.get("caseSensitive", False)
    if not isinstance(is_regex, bool) or not isinstance(case_sensitive, bool):
        raise ToolFailure("invalid_arguments", "search flags must be booleans")
    glob_pattern = arguments.get("glob")
    if glob_pattern is not None and not isinstance(glob_pattern, str):
        raise ToolFailure("invalid_arguments", "glob must be a string")
    limit = optional_int(arguments, "limit", 100, 1, 500)

    root = paths.resolve(raw_path)
    if not root.exists():
        raise ToolFailure("file_not_found", f"search path does not exist: {raw_path}", retryable=True)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if is_regex else re.escape(query), flags)
    except re.error as exc:
        raise ToolFailure("invalid_arguments", f"invalid regular expression: {exc}") from exc

    ignored = {
        ".git",
        ".coding-agent",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
    }
    candidates = [root] if root.is_file() else root.rglob("*")
    matches: list[dict[str, Any]] = []
    paths_found: list[str] = []
    total = 0

    for candidate in candidates:
        if any(part in ignored for part in candidate.relative_to(paths.root).parts):
            continue
        if not candidate.is_file():
            continue
        display = paths.display(candidate)
        if glob_pattern and not candidate.match(glob_pattern):
            continue
        if mode == "files":
            if pattern.search(candidate.name):
                total += 1
                if len(paths_found) < limit:
                    paths_found.append(display)
            continue

        try:
            if candidate.stat().st_size > 2_000_000:
                continue
            text, _ = _read_utf8(candidate)
        except (ToolFailure, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = pattern.search(line)
            if not match:
                continue
            total += 1
            if len(matches) < limit:
                matches.append(
                    {
                        "path": display,
                        "line": line_number,
                        "column": match.start() + 1,
                        "text": line[:500],
                    }
                )

    truncated = total > limit
    data: dict[str, Any] = {
        "mode": mode,
        "matchCount": total,
        "truncated": truncated,
    }
    data["paths" if mode == "files" else "matches"] = paths_found if mode == "files" else matches
    return ToolResult(
        ok=True,
        summary=f"Found {total} {mode} matches for {query!r}",
        data=data,
        truncated=truncated,
    )


def write_file(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    raw_path = require_string(arguments, "path")
    content = require_string(arguments, "content", allow_empty=True)
    expected_hash = arguments.get("expectedHash")
    if expected_hash is not None and not isinstance(expected_hash, str):
        raise ToolFailure("invalid_arguments", "expectedHash must be a string")
    create_parents = arguments.get("createParents", False)
    if not isinstance(create_parents, bool):
        raise ToolFailure("invalid_arguments", "createParents must be a boolean")

    path = paths.resolve(raw_path, for_creation=True)
    existed = path.exists()
    before = ""
    if existed:
        before, raw = _read_utf8(path)
        if expected_hash and _hash_bytes(raw) != expected_hash:
            raise ToolFailure(
                "conflict", "file changed since it was read; read it again before writing", retryable=True
            )
    elif not path.parent.exists():
        if not create_parents:
            raise ToolFailure("file_not_found", "parent directory does not exist", retryable=True)
        path.parent.mkdir(parents=True)

    _atomic_write(path, content)
    display = paths.display(path)
    diff, diff_truncated = _unified_diff(display, before, content)
    return ToolResult(
        ok=True,
        summary=f"{'Updated' if existed else 'Created'} {display}",
        data={
            "path": display,
            "created": not existed,
            "bytesWritten": len(content.encode("utf-8")),
            "contentHash": _hash_bytes(content.encode("utf-8")),
            "diff": diff,
            "diffTruncated": diff_truncated,
        },
        truncated=diff_truncated,
        changed_files=[display],
    )


def move_path(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    source_raw = require_string(arguments, "source")
    destination_raw = require_string(arguments, "destination")
    create_parents = arguments.get("createParents", False)
    if not isinstance(create_parents, bool):
        raise ToolFailure("invalid_arguments", "createParents must be a boolean")
    source = paths.resolve_entry(source_raw)
    if source.is_symlink():
        paths.resolve(source_raw)  # Reject moving a symlink whose target escapes the workspace.
    destination = paths.resolve(destination_raw, for_creation=True)
    if source == paths.root:
        raise ToolFailure("permission_denied", "cannot move the workspace root")
    if not source.exists() and not source.is_symlink():
        raise ToolFailure("file_not_found", f"source does not exist: {source_raw}", retryable=True)
    if destination.exists() or destination.is_symlink():
        raise ToolFailure("conflict", f"destination already exists: {destination_raw}")
    if source.is_dir() and destination.is_relative_to(source):
        raise ToolFailure("invalid_arguments", "cannot move a directory into itself")
    if not destination.parent.exists():
        if not create_parents:
            raise ToolFailure("file_not_found", "destination parent does not exist", retryable=True)
        destination.parent.mkdir(parents=True)
    source_display = source.relative_to(paths.root).as_posix()
    destination_display = paths.display(destination)
    shutil.move(str(source), str(destination))
    return ToolResult(
        ok=True,
        summary=f"Moved {source_display} to {destination_display}",
        data={"source": source_display, "destination": destination_display},
        changed_files=[source_display, destination_display],
    )


def delete_path(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    raw_path = require_string(arguments, "path")
    recursive = arguments.get("recursive", False)
    if not isinstance(recursive, bool):
        raise ToolFailure("invalid_arguments", "recursive must be a boolean")
    path = paths.resolve_entry(raw_path)
    if path == paths.root:
        raise ToolFailure("permission_denied", "cannot delete the workspace root")
    if not path.exists() and not path.is_symlink():
        raise ToolFailure("file_not_found", f"path does not exist: {raw_path}", retryable=True)
    display = path.relative_to(paths.root).as_posix()
    kind = "directory" if path.is_dir() and not path.is_symlink() else "file"
    if kind == "directory":
        if recursive:
            shutil.rmtree(path)
        else:
            try:
                path.rmdir()
            except OSError as exc:
                raise ToolFailure(
                    "directory_not_empty",
                    "directory is not empty; set recursive=true to delete it",
                ) from exc
    else:
        path.unlink()
    return ToolResult(
        ok=True,
        summary=f"Deleted {display}",
        data={"path": display, "type": kind, "recursive": recursive},
        changed_files=[display],
    )
