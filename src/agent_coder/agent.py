from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .config import AgentConfig
from .context import ContextManager
from .model import AssistantReply, ModelClientProtocol, ToolCall
from .protocol import ProtocolEmitter, new_id
from .tools import ToolRegistry, ToolResult


ApprovalCallback = Callable[[str, dict[str, Any]], Awaitable[bool]]
PersistCallback = Callable[[list[dict[str, Any]]], Awaitable[None]]


SYSTEM_PROMPT = """You are a local coding agent operating inside one workspace.
Use the provided tools to inspect facts, edit files, and run verification. Do not claim a file
was read, changed, or tested unless a tool result confirms it. Read relevant code before editing.
Prefer apply_patch for local edits and write_file for new files or full replacements. Paths must
stay inside the workspace. Never seek credentials or bypass a denied approval. After changes,
run focused tests or checks when available. When the task is complete, respond with a concise
summary of changes, verification, and any remaining limitation. The apply_patch format is:
*** Begin Patch
*** Update File: relative/path
@@
 context line
-old line
+new line
*** End Patch
For new files use *** Add File and prefix each content line with +.
"""


@dataclass(slots=True)
class TurnState:
    turn_id: str
    user_text: str
    step: int = 0
    tool_calls: int = 0
    changed_files: set[str] = field(default_factory=set)
    recent_fingerprints: list[tuple[str, int]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    terminal: bool = False


class Agent:
    def __init__(
        self,
        *,
        config: AgentConfig,
        session_id: str,
        emitter: ProtocolEmitter,
        model: ModelClientProtocol,
        tools: ToolRegistry,
        request_approval: ApprovalCallback,
        history_messages: list[dict[str, Any]] | None = None,
        persist_messages: PersistCallback | None = None,
    ) -> None:
        self.config = config
        self.session_id = session_id
        self.emitter = emitter
        self.model = model
        self.tools = tools
        self.request_approval = request_approval
        self.persist_messages = persist_messages
        self.context = ContextManager(config.max_context_chars)
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + f"\nWorkspace root: {config.workspace_root}\n"
                + "The target platform is macOS/Linux and commands are non-interactive.",
            }
        ]
        self.messages.extend(history_messages or [])

    async def run_turn(self, turn_id: str, user_text: str) -> None:
        turn = TurnState(turn_id=turn_id, user_text=user_text)
        self.messages.append({"role": "user", "content": user_text})
        await self._persist()
        await self._emit("turn_started", {"text": user_text}, turn)
        try:
            await self._run_loop(turn)
        except asyncio.CancelledError:
            if not turn.terminal:
                turn.terminal = True
                await self._emit("turn_cancelled", {"reason": "user_requested"}, turn)
        except Exception as exc:
            if not turn.terminal:
                turn.terminal = True
                await self._emit(
                    "turn_failed",
                    {
                        "error": {
                            "code": "core_internal_error",
                            "message": f"{type(exc).__name__}: {exc}",
                            "retryable": False,
                        },
                        "steps": turn.step,
                        "toolCalls": turn.tool_calls,
                    },
                    turn,
                )

    async def _run_loop(self, turn: TurnState) -> None:
        empty_responses = 0
        while True:
            if turn.step >= self.config.max_steps:
                await self._fail(turn, "max_steps_exceeded", "Agent reached the maximum step count")
                return
            turn.step += 1
            await self._emit(
                "agent_status", {"status": "requesting_model", "step": turn.step}, turn
            )

            assistant_message_id = new_id("asst")
            await self._emit(
                "assistant_message_started",
                {"assistantMessageId": assistant_message_id, "step": turn.step},
                turn,
            )

            async def on_delta(text: str) -> None:
                await self._emit(
                    "assistant_delta",
                    {"assistantMessageId": assistant_message_id, "text": text},
                    turn,
                )

            reply = await self.model.complete(
                self.context.build_request(self.messages), self.tools.schemas, on_delta
            )
            await self._emit(
                "assistant_message_finished",
                {"assistantMessageId": assistant_message_id, "text": reply.content},
                turn,
            )
            self.messages.append(reply.as_message())

            if reply.tool_calls:
                empty_responses = 0
                for call in reply.tool_calls:
                    result = await self._execute_call(turn, call)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result.as_dict(), ensure_ascii=False),
                        }
                    )
                    if turn.terminal:
                        await self._persist()
                        return
                await self._persist()
                continue

            if reply.content.strip():
                await self._persist()
                turn.terminal = True
                await self._emit(
                    "turn_finished",
                    {
                        "status": "completed",
                        "finalText": reply.content,
                        "steps": turn.step,
                        "toolCalls": turn.tool_calls,
                        "changedFiles": sorted(turn.changed_files),
                        "durationMs": int((time.monotonic() - turn.started_at) * 1000),
                    },
                    turn,
                )
                return

            empty_responses += 1
            if empty_responses >= 2:
                await self._fail(turn, "empty_model_response", "Model returned no text or tool calls")
                return
            self.messages.append(
                {
                    "role": "user",
                    "content": "Your response was empty. Continue the task using tools or provide a final answer.",
                }
            )
            await self._persist()

    async def _persist(self) -> None:
        if self.persist_messages is not None:
            await self.persist_messages(self.messages[1:])

    async def _execute_call(self, turn: TurnState, call: ToolCall) -> ToolResult:
        turn.tool_calls += 1
        try:
            arguments = json.loads(call.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            result = ToolResult(
                ok=False,
                summary="Tool arguments are not valid JSON",
                error_code="invalid_arguments",
                error_message=str(exc),
                retryable=True,
            )
            await self._emit_tool_requested(turn, call, {}, "high")
            await self._emit_tool_finished(turn, call, result, 0)
            return result

        risk = self.tools.risk(call.name, arguments)
        await self._emit_tool_requested(turn, call, arguments, risk)
        if risk == "forbidden":
            result = ToolResult(
                ok=False,
                summary="Tool call is forbidden by the safety policy",
                error_code="permission_denied",
                error_message="operation is outside the allowed tool policy",
            )
            await self._emit_tool_finished(turn, call, result, 0)
            return result

        if risk == "high":
            await self._emit(
                "agent_status", {"status": "waiting_for_approval", "step": turn.step}, turn
            )
            allowed = await self.request_approval(
                call.id,
                {
                    "name": call.name,
                    "summary": self._tool_summary(call.name, arguments),
                    "reason": "This operation may modify dependencies, delete data, or cause external effects.",
                    "arguments": arguments,
                },
            )
            if not allowed:
                result = ToolResult(
                    ok=False,
                    summary="User denied this tool call",
                    error_code="approval_denied",
                    error_message="user denied this operation",
                )
                await self._emit_tool_finished(turn, call, result, 0)
                return result

        await self._emit(
            "agent_status", {"status": "running_tool", "step": turn.step}, turn
        )
        await self._emit("tool_started", {"name": call.name}, turn, call.id)

        async def on_output(stream: str, text: str) -> None:
            # Prevent one event from becoming unbounded; final output still carries head/tail.
            await self._emit(
                "tool_output_delta",
                {"stream": stream, "text": text[-4_000:]},
                turn,
                call.id,
            )

        result, duration_ms = await self.tools.execute(call.name, arguments, on_output=on_output)
        turn.changed_files.update(result.changed_files)
        fingerprint = (
            call.name + ":" + json.dumps(arguments, sort_keys=True, ensure_ascii=False),
            len(turn.changed_files),
        )
        turn.recent_fingerprints.append(fingerprint)
        turn.recent_fingerprints = turn.recent_fingerprints[-3:]

        if result.ok and result.data and result.data.get("diff"):
            diff = str(result.data["diff"])
            await self._emit(
                "file_diff",
                {
                    "path": result.changed_files[0] if len(result.changed_files) == 1 else "multiple files",
                    "diff": diff,
                    "addedLines": sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")),
                    "removedLines": sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")),
                    "truncated": result.truncated,
                },
                turn,
                call.id,
            )
        await self._emit_tool_finished(turn, call, result, duration_ms)

        if len(turn.recent_fingerprints) == 3 and len(set(turn.recent_fingerprints)) == 1:
            await self._fail(
                turn, "repeated_tool_call", "Agent repeated the same tool call without progress"
            )
        return result

    async def _emit_tool_requested(
        self, turn: TurnState, call: ToolCall, arguments: dict[str, Any], risk: str
    ) -> None:
        await self._emit(
            "tool_requested",
            {"step": turn.step, "name": call.name, "arguments": arguments, "risk": risk},
            turn,
            call.id,
        )

    async def _emit_tool_finished(
        self, turn: TurnState, call: ToolCall, result: ToolResult, duration_ms: int
    ) -> None:
        payload = {
            "name": call.name,
            "ok": result.ok,
            "durationMs": duration_ms,
            "summary": result.summary,
            "result": result.data,
            "error": None
            if result.ok
            else {
                "code": result.error_code,
                "message": result.error_message,
                "retryable": result.retryable,
            },
        }
        await self._emit("tool_finished", payload, turn, call.id)

    async def _fail(self, turn: TurnState, code: str, message: str) -> None:
        if turn.terminal:
            return
        turn.terminal = True
        await self._emit(
            "turn_failed",
            {
                "error": {"code": code, "message": message, "retryable": False},
                "steps": turn.step,
                "toolCalls": turn.tool_calls,
            },
            turn,
        )

    async def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        turn: TurnState,
        tool_call_id: str | None = None,
    ) -> None:
        await self.emitter.emit(
            event_type,
            payload,
            session_id=self.session_id,
            turn_id=turn.turn_id,
            tool_call_id=tool_call_id,
        )

    @staticmethod
    def _tool_summary(name: str, arguments: dict[str, Any]) -> str:
        if name == "run_command":
            return f"Run command: {arguments.get('command', '')}"
        if name in {"write_file", "read_file", "list_directory"}:
            return f"{name}: {arguments.get('path', '.')}"
        if name == "apply_patch":
            return "Apply a patch that includes a high-risk file deletion"
        return name
