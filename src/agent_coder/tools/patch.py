from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .base import ToolFailure, ToolResult, WorkspacePaths, require_string
from .filesystem import _atomic_write, _read_utf8


Action = Literal["add", "update", "delete"]


@dataclass(slots=True)
class PatchSection:
    action: Action
    path: str
    lines: list[str]


def parse_patch(text: str) -> list[PatchSection]:
    lines = text.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ToolFailure(
            "invalid_arguments", "patch must start with '*** Begin Patch' and end with '*** End Patch'"
        )

    sections: list[PatchSection] = []
    current: PatchSection | None = None
    markers = {
        "*** Add File: ": "add",
        "*** Update File: ": "update",
        "*** Delete File: ": "delete",
    }
    for line in lines[1:-1]:
        matched = False
        for prefix, action in markers.items():
            if line.startswith(prefix):
                if current is not None:
                    sections.append(current)
                path = line[len(prefix) :].strip()
                if not path:
                    raise ToolFailure("invalid_arguments", "patch section path cannot be empty")
                current = PatchSection(action=action, path=path, lines=[])
                matched = True
                break
        if matched:
            continue
        if current is None:
            if line.strip():
                raise ToolFailure("invalid_arguments", "content appears before a patch file section")
            continue
        current.lines.append(line)

    if current is not None:
        sections.append(current)
    if not sections:
        raise ToolFailure("invalid_arguments", "patch contains no file sections")
    return sections


def _find_sequence(haystack: list[str], needle: list[str], start: int) -> int:
    if not needle:
        return start
    for index in range(start, len(haystack) - len(needle) + 1):
        if haystack[index : index + len(needle)] == needle:
            return index
    return -1


def _apply_update(original: str, lines: list[str], path: str) -> str:
    source = original.splitlines()
    had_trailing_newline = original.endswith("\n")
    output: list[str] = []
    cursor = 0
    hunk: list[str] = []

    def apply_hunk(items: list[str], cursor_value: int) -> int:
        if not items:
            return cursor_value
        old = [line[1:] for line in items if line.startswith((" ", "-"))]
        new = [line[1:] for line in items if line.startswith((" ", "+"))]
        if any(not line.startswith((" ", "+", "-")) for line in items):
            raise ToolFailure("invalid_arguments", f"invalid patch line for {path}")
        location = _find_sequence(source, old, cursor_value)
        if location < 0:
            raise ToolFailure(
                "conflict", f"patch context does not match {path}; read the file again", retryable=True
            )
        output.extend(source[cursor_value:location])
        output.extend(new)
        return location + len(old)

    for line in lines:
        if line.startswith("@@"):
            cursor = apply_hunk(hunk, cursor)
            hunk = []
        else:
            hunk.append(line)
    cursor = apply_hunk(hunk, cursor)
    output.extend(source[cursor:])
    result = "\n".join(output)
    if had_trailing_newline:
        result += "\n"
    return result


def apply_patch(paths: WorkspacePaths, arguments: dict[str, Any]) -> ToolResult:
    patch_text = require_string(arguments, "patch")
    sections = parse_patch(patch_text)
    planned: list[tuple[PatchSection, Path, str | None, str | None]] = []

    for section in sections:
        target = paths.resolve(section.path, for_creation=section.action == "add")
        before: str | None = None
        after: str | None = None
        if section.action == "add":
            if target.exists():
                raise ToolFailure("conflict", f"file already exists: {section.path}", retryable=True)
            if not target.parent.exists():
                raise ToolFailure("file_not_found", f"parent directory does not exist: {section.path}")
            if any(line and not line.startswith("+") for line in section.lines):
                raise ToolFailure("invalid_arguments", f"added file lines must start with +: {section.path}")
            after = "\n".join(line[1:] if line.startswith("+") else "" for line in section.lines)
            if section.lines:
                after += "\n"
        elif section.action == "update":
            before, _ = _read_utf8(target)
            after = _apply_update(before, section.lines, section.path)
        else:
            before, _ = _read_utf8(target)
        planned.append((section, target, before, after))

    changed: list[str] = []
    diff_parts: list[str] = []
    backups: list[tuple[Path, str | None]] = []
    try:
        for section, target, before, after in planned:
            backups.append((target, before))
            display = paths.display(target)
            if section.action == "delete":
                target.unlink()
                new_lines: list[str] = []
            else:
                assert after is not None
                _atomic_write(target, after)
                new_lines = after.splitlines(keepends=True)
            diff_parts.extend(
                difflib.unified_diff(
                    (before or "").splitlines(keepends=True),
                    new_lines,
                    fromfile=f"a/{display}" if before is not None else "/dev/null",
                    tofile=f"b/{display}" if after is not None else "/dev/null",
                )
            )
            changed.append(display)
    except Exception:
        for target, before in reversed(backups):
            if before is None:
                if target.exists():
                    target.unlink()
            else:
                _atomic_write(target, before)
        raise

    diff = "".join(diff_parts)
    truncated = len(diff) > 60_000
    if truncated:
        diff = diff[:30_000] + "\n... diff truncated ...\n" + diff[-30_000:]
    return ToolResult(
        ok=True,
        summary=f"Patched {len(changed)} file(s)",
        data={"files": changed, "diff": diff, "diffTruncated": truncated},
        truncated=truncated,
        changed_files=changed,
    )


def patch_contains_delete(arguments: dict[str, Any]) -> bool:
    patch_text = arguments.get("patch")
    return isinstance(patch_text, str) and "*** Delete File:" in patch_text

