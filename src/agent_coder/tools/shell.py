from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from .base import Risk, ToolFailure, ToolResult, WorkspacePaths, optional_int, require_string


OutputCallback = Callable[[str, str], Awaitable[None]]

_FORBIDDEN = (
    re.compile(r"(^|\s)sudo(\s|$)"),
    re.compile(r"(^|\s)git\s+push(\s|$)"),
    re.compile(r"(^|\s)(env|printenv)(\s|$)"),
    re.compile(r"OPENAI_API_KEY|AGENT_MODEL", re.IGNORECASE),
    re.compile(r"(^|\s)(nohup|disown)(\s|$)"),
    re.compile(r"\$\(|`"),
)

_HIGH_RISK = (
    re.compile(r"(^|\s)(rm|rmdir|mv|chmod|chown)(\s|$)"),
    re.compile(r"(^|\s)(npm|pnpm|yarn)\s+(install|add|remove|update|upgrade)(\s|$)"),
    re.compile(r"(^|\s)uv\s+(add|remove|sync|lock)(\s|$)"),
    re.compile(r"(^|\s)(pip|pip3)\s+install(\s|$)"),
    re.compile(r"(^|\s)(curl|wget|ssh|scp)(\s|$)"),
    re.compile(r"(^|\s)git\s+(commit|reset|checkout|switch|clean|rebase)(\s|$)"),
    re.compile(r"[<>]|[;&|]"),
)

_MEDIUM_RISK = (
    re.compile(r"(^|\s)(ruff|black|prettier)(\s|$)"),
    re.compile(r"(^|\s)(npm|pnpm|yarn)\s+run\s+(build|format|fix)(\s|$)"),
)

_LOW_RISK = (
    re.compile(r"^(pwd|ls)(\s|$)"),
    re.compile(r"^(rg|grep|find)(\s|$)"),
    re.compile(r"^git\s+(status|diff|log|show)(\s|$)"),
    re.compile(r"^(pytest|python\s+-m\s+pytest|uv\s+run\s+pytest)(\s|$)"),
    re.compile(r"^(npm|pnpm|yarn)\s+(test|run\s+(test|typecheck|lint))(\s|$)"),
    re.compile(r"^(cargo\s+test|go\s+test)(\s|$)"),
)


def command_risk(command: str) -> Risk:
    if any(pattern.search(command) for pattern in _FORBIDDEN):
        return "forbidden"
    if any(pattern.search(command) for pattern in _HIGH_RISK):
        return "high"
    if any(pattern.search(command) for pattern in _MEDIUM_RISK):
        return "medium"
    if any(pattern.search(command.strip()) for pattern in _LOW_RISK):
        return "low"
    return "high"


def _safe_environment() -> dict[str, str]:
    sensitive = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.IGNORECASE)
    return {key: value for key, value in os.environ.items() if not sensitive.search(key)}


async def run_command(
    paths: WorkspacePaths,
    arguments: dict[str, Any],
    *,
    default_timeout_ms: int,
    on_output: OutputCallback,
) -> ToolResult:
    command = require_string(arguments, "command")
    raw_cwd = arguments.get("cwd", ".")
    if not isinstance(raw_cwd, str):
        raise ToolFailure("invalid_arguments", "cwd must be a string")
    timeout_ms = optional_int(arguments, "timeoutMs", default_timeout_ms, 1_000, 120_000)
    cwd = paths.resolve(raw_cwd)
    if not cwd.is_dir():
        raise ToolFailure("not_a_directory", f"not a directory: {raw_cwd}", retryable=True)
    _ensure_command_scope(command, paths.root)
    if command_risk(command) == "forbidden":
        raise ToolFailure("permission_denied", "command is forbidden by the safety policy")

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        env=_safe_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    async def consume(stream: asyncio.StreamReader, name: str, target: list[str]) -> None:
        while chunk := await stream.read(4096):
            text = chunk.decode("utf-8", errors="replace")
            target.append(text)
            await on_output(name, text)

    consumers = [
        asyncio.create_task(consume(process.stdout, "stdout", stdout_parts)),  # type: ignore[arg-type]
        asyncio.create_task(consume(process.stderr, "stderr", stderr_parts)),  # type: ignore[arg-type]
    ]
    timed_out = False
    cancelled = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000)
    except TimeoutError:
        timed_out = True
        _terminate_process_group(process.pid)
        await process.wait()
    except asyncio.CancelledError:
        cancelled = True
        _terminate_process_group(process.pid)
        await process.wait()
        raise
    finally:
        await asyncio.gather(*consumers, return_exceptions=True)

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    stdout, stdout_truncated = _limit_output(stdout)
    stderr, stderr_truncated = _limit_output(stderr)
    truncated = stdout_truncated or stderr_truncated
    data = {
        "command": command,
        "cwd": paths.display(cwd),
        "exitCode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": timed_out,
        "cancelled": cancelled,
        "truncated": truncated,
    }
    if timed_out:
        return ToolResult(
            ok=False,
            summary=f"Command timed out after {timeout_ms} ms",
            data=data,
            error_code="command_timeout",
            error_message="command exceeded its timeout",
            retryable=True,
            truncated=truncated,
        )
    if process.returncode != 0:
        return ToolResult(
            ok=False,
            summary=f"Command exited with code {process.returncode}",
            data=data,
            error_code="command_failed",
            error_message=f"command exited with code {process.returncode}",
            retryable=True,
            truncated=truncated,
        )
    return ToolResult(
        ok=True,
        summary=f"Command completed with exit code 0",
        data=data,
        truncated=truncated,
    )


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _ensure_command_scope(command: str, workspace_root) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ToolFailure("invalid_arguments", f"invalid shell command: {exc}") from exc
    for index, token in enumerate(tokens):
        if token == ".." or token.startswith("../") or "/../" in token or token.startswith("~"):
            raise ToolFailure("permission_denied", "command references a path outside the workspace")
        if ".coding-agent" in token.lower():
            raise ToolFailure("permission_denied", "command references protected session history")
        if not token.startswith("/"):
            continue
        candidate = os.path.realpath(token)
        if index == 0 and candidate.startswith(("/bin/", "/usr/bin/", "/opt/homebrew/bin/")):
            continue
        try:
            if os.path.commonpath([str(workspace_root), candidate]) == str(workspace_root):
                continue
        except ValueError:
            pass
        raise ToolFailure("permission_denied", "command references an absolute path outside the workspace")


def _limit_output(text: str, limit: int = 20_000) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n... output truncated ...\n" + text[-half:], True
