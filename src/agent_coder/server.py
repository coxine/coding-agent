from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, TextIO

from .agent import Agent
from .config import AgentConfig, ConfigurationError
from .model import OpenAICompatibleClient
from .protocol import PROTOCOL_VERSION, ProtocolEmitter, new_id, validate_envelope
from .tools import ToolRegistry


class CoreServer:
    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        emitter: ProtocolEmitter | None = None,
    ) -> None:
        self.input_stream = input_stream or sys.stdin
        self.emitter = emitter or ProtocolEmitter()
        self.session_id: str | None = None
        self.config: AgentConfig | None = None
        self.agent: Agent | None = None
        self.active_turn_id: str | None = None
        self.active_task: asyncio.Task[None] | None = None
        self.approvals: dict[str, asyncio.Future[bool]] = {}
        self.shutting_down = False

    async def run(self) -> int:
        while not self.shutting_down:
            line = await asyncio.to_thread(self.input_stream.readline)
            if line == "":
                await self._shutdown(emit_complete=False)
                break
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                await self._error("invalid_json", f"invalid JSON: {exc.msg}")
                continue

            validation_error = validate_envelope(message)
            if validation_error:
                fatal = message.get("protocolVersion") != PROTOCOL_VERSION
                await self._error(
                    "protocol_version_mismatch" if fatal else "invalid_message",
                    validation_error,
                    fatal=fatal,
                    related_message_id=message.get("messageId") if isinstance(message, dict) else None,
                )
                if fatal:
                    return 3
                continue

            await self._dispatch(message)
        return 0

    async def _dispatch(self, message: dict[str, Any]) -> None:
        message_type = message["type"]
        if message_type == "initialize":
            await self._initialize(message)
        elif message_type == "submit_task":
            await self._submit_task(message)
        elif message_type == "approval_response":
            await self._approval_response(message)
        elif message_type == "cancel_turn":
            await self._cancel_turn(message)
        elif message_type == "shutdown":
            await self._shutdown(emit_complete=True)
        else:
            await self._error(
                "invalid_message",
                f"unknown message type: {message_type}",
                related_message_id=message.get("messageId"),
            )

    async def _initialize(self, message: dict[str, Any]) -> None:
        if self.session_id is not None:
            await self._error("already_initialized", "core is already initialized")
            return
        try:
            config = AgentConfig.from_initialize(message["payload"])
        except ConfigurationError as exc:
            await self._error("configuration_error", str(exc), fatal=False)
            return

        self.session_id = new_id("sess")
        self.config = config
        model = OpenAICompatibleClient(
            api_key=config.api_key, base_url=config.base_url, model=config.model
        )
        tools = ToolRegistry(config.workspace_root, command_timeout_ms=config.command_timeout_ms)
        self.agent = Agent(
            config=config,
            session_id=self.session_id,
            emitter=self.emitter,
            model=model,
            tools=tools,
            request_approval=self._request_approval,
        )
        await self.emitter.emit(
            "initialized",
            {
                "workspaceRoot": str(config.workspace_root),
                "model": config.model,
                "capabilities": {
                    "streaming": True,
                    "toolCalling": True,
                    "approvals": True,
                },
            },
            session_id=self.session_id,
        )

    async def _submit_task(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if self.agent is None:
            await self._error("not_initialized", "core is not initialized")
            return
        if self.active_task and not self.active_task.done():
            await self._error("turn_already_running", "a turn is already running")
            return
        turn_id = message.get("turnId")
        text = message["payload"].get("text")
        if not isinstance(turn_id, str) or not turn_id:
            await self._error("invalid_message", "turnId is required")
            return
        if not isinstance(text, str) or not text.strip():
            await self._error("invalid_message", "payload.text must be a non-empty string")
            return

        self.active_turn_id = turn_id
        self.active_task = asyncio.create_task(self.agent.run_turn(turn_id, text.strip()))
        self.active_task.add_done_callback(self._turn_done)

    async def _request_approval(self, tool_call_id: str, payload: dict[str, Any]) -> bool:
        if self.session_id is None or self.active_turn_id is None:
            return False
        future = asyncio.get_running_loop().create_future()
        self.approvals[tool_call_id] = future
        await self.emitter.emit(
            "approval_required",
            payload,
            session_id=self.session_id,
            turn_id=self.active_turn_id,
            tool_call_id=tool_call_id,
        )
        try:
            return await future
        finally:
            self.approvals.pop(tool_call_id, None)

    async def _approval_response(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if message.get("turnId") != self.active_turn_id:
            await self._error("unknown_turn", "turnId is not active")
            return
        tool_call_id = message.get("toolCallId")
        future = self.approvals.get(tool_call_id)
        if future is None or future.done():
            await self._error("approval_not_pending", "tool call is not waiting for approval")
            return
        decision = message["payload"].get("decision")
        if decision not in {"allow_once", "deny"}:
            await self._error("invalid_message", "decision must be allow_once or deny")
            return
        future.set_result(decision == "allow_once")

    async def _cancel_turn(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if message.get("turnId") != self.active_turn_id or self.active_task is None:
            await self._error("unknown_turn", "turnId is not active")
            return
        if not self.active_task.done():
            self.active_task.cancel()

    async def _shutdown(self, *, emit_complete: bool) -> None:
        self.shutting_down = True
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
        for future in self.approvals.values():
            if not future.done():
                future.cancel()
        self.approvals.clear()
        if emit_complete:
            await self.emitter.emit(
                "shutdown_complete", {}, session_id=self.session_id
            )

    def _turn_done(self, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
        self.active_turn_id = None
        self.active_task = None

    async def _check_session(self, message: dict[str, Any]) -> bool:
        if self.session_id is None:
            await self._error("not_initialized", "core is not initialized")
            return False
        if message.get("sessionId") != self.session_id:
            await self._error("session_mismatch", "sessionId does not match")
            return False
        return True

    async def _error(
        self,
        code: str,
        message: str,
        *,
        fatal: bool = False,
        related_message_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"code": code, "message": message, "fatal": fatal}
        if related_message_id:
            payload["relatedMessageId"] = related_message_id
        await self.emitter.emit("error", payload, session_id=self.session_id)


async def run_server() -> int:
    return await CoreServer().run()
