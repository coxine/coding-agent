from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, TextIO

from .agent import Agent
from .config import AgentConfig, ConfigurationError
from .model import OpenAICompatibleClient
from .protocol import PROTOCOL_VERSION, ProtocolEmitter, new_id, validate_envelope
from .sessions import Conversation, SessionStore
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
        self.session_store: SessionStore | None = None
        self.conversation: Conversation | None = None
        self.active_turn_id: str | None = None
        self.active_task: asyncio.Task[None] | None = None
        self.approvals: dict[str, asyncio.Future[bool]] = {}
        self.questions: dict[str, asyncio.Future[str | None]] = {}
        self.turn_resume_event = asyncio.Event()
        self.turn_resume_event.set()
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
        elif message_type == "user_input_response":
            await self._user_input_response(message)
        elif message_type == "cancel_turn":
            await self._cancel_turn(message)
        elif message_type == "pause_turn":
            await self._pause_turn(message)
        elif message_type == "resume_turn":
            await self._resume_turn(message)
        elif message_type == "list_sessions":
            await self._list_sessions(message)
        elif message_type == "get_status":
            await self._get_status(message)
        elif message_type == "switch_session":
            await self._switch_session(message)
        elif message_type == "create_session":
            await self._create_session(message)
        elif message_type == "rename_session":
            await self._rename_session(message)
        elif message_type == "delete_session":
            await self._delete_session(message)
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
        self.session_store = SessionStore(config.workspace_root)
        self.conversation = self.session_store.latest_or_create()
        self.agent = self._build_agent(self.conversation)
        await self.emitter.emit(
            "initialized",
            {
                "workspaceRoot": str(config.workspace_root),
                "model": config.model,
                "conversationId": self.conversation.id,
                "conversationTitle": self.conversation.title,
                "titleSource": self.conversation.title_source,
                "transcript": self.session_store.transcript(self.conversation.messages),
                "capabilities": {
                    "streaming": True,
                    "toolCalling": True,
                    "approvals": True,
                    "sessions": True,
                    "userInput": True,
                    "pauseResume": True,
                },
            },
            session_id=self.session_id,
        )

    def _build_agent(self, conversation: Conversation) -> Agent:
        assert self.config is not None and self.session_id is not None
        model = OpenAICompatibleClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
        )
        tools = ToolRegistry(
            self.config.workspace_root, command_timeout_ms=self.config.command_timeout_ms
        )

        async def persist(messages: list[dict[str, Any]], compact_state: dict[str, Any]) -> None:
            if self.session_store is not None and self.conversation is conversation:
                previous_title = conversation.title
                await asyncio.to_thread(
                    self.session_store.update_messages,
                    conversation,
                    messages,
                    compact_state,
                )
                if conversation.title != previous_title:
                    await self.emitter.emit(
                        "conversation_updated",
                        {
                            "conversationId": conversation.id,
                            "conversationTitle": conversation.title,
                            "titleSource": conversation.title_source,
                        },
                        session_id=self.session_id,
                    )

        async def persist_usage(turn_id: str, step: int, usage: Any) -> None:
            if self.session_store is not None and self.conversation is conversation:
                await asyncio.to_thread(
                    self.session_store.record_usage,
                    conversation,
                    turn_id=turn_id,
                    step=step,
                    usage=usage.as_dict() if usage is not None else None,
                )

        return Agent(
            config=self.config,
            session_id=self.session_id,
            emitter=self.emitter,
            model=model,
            tools=tools,
            request_approval=self._request_approval,
            request_user_input=self._request_user_input,
            history_messages=conversation.messages,
            compact_state=conversation.compact_state,
            persist_messages=persist,
            persist_usage=persist_usage,
            wait_until_resumed=self._wait_until_resumed,
        )

    async def _list_sessions(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if self.session_store is None or self.conversation is None:
            return
        sessions = await asyncio.to_thread(self.session_store.list)
        await self.emitter.emit(
            "sessions_listed",
            {"activeConversationId": self.conversation.id, "sessions": sessions},
            session_id=self.session_id,
        )

    async def _get_status(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if self.config is None or self.conversation is None or self.agent is None:
            await self._error("not_initialized", "core is not initialized")
            return
        token_usage = (
            self.session_store.usage_summary(self.conversation)
            if self.session_store is not None
            else {}
        )
        token_usage["contextWindowTokens"] = self.config.context_window_tokens
        await self.emitter.emit(
            "status_report",
            {
                "model": self.config.model,
                "workspaceRoot": str(self.config.workspace_root),
                "coreSessionId": self.session_id,
                "conversationId": self.conversation.id,
                "context": self.agent.context_stats(),
                "tokenUsage": token_usage,
                "metadata": {
                    "conversationTitle": self.conversation.title,
                    "createdAt": self.conversation.created_at,
                    "updatedAt": self.conversation.updated_at,
                    "tools": len(self.agent.tools.schemas),
                    "maxSteps": self.config.max_steps,
                    "permissions": "Workspace (approval required for high-risk tools)",
                },
            },
            session_id=self.session_id,
        )

    async def _switch_session(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message) or not await self._can_change_conversation():
            return
        conversation_id = message["payload"].get("conversationId")
        if not isinstance(conversation_id, str):
            await self._error("invalid_message", "payload.conversationId must be a string")
            return
        assert self.session_store is not None
        try:
            conversation = await asyncio.to_thread(self.session_store.load, conversation_id)
        except ValueError as exc:
            await self._error("unknown_conversation", str(exc))
            return
        await asyncio.to_thread(
            self.session_store.update_messages, conversation, conversation.messages
        )
        self.conversation = conversation
        self.agent = self._build_agent(conversation)
        await self._emit_conversation("conversation_switched")

    async def _create_session(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message) or not await self._can_change_conversation():
            return
        assert self.session_store is not None
        self.conversation = await asyncio.to_thread(self.session_store.create)
        self.agent = self._build_agent(self.conversation)
        await self._emit_conversation("conversation_created")

    async def _rename_session(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message) or not await self._can_change_conversation():
            return
        name = message["payload"].get("name")
        if not isinstance(name, str):
            await self._error("invalid_message", "payload.name must be a string")
            return
        assert self.session_store is not None and self.conversation is not None
        try:
            await asyncio.to_thread(self.session_store.rename, self.conversation, name)
        except ValueError as exc:
            await self._error("invalid_message", str(exc))
            return
        await self.emitter.emit(
            "conversation_updated",
            {
                "conversationId": self.conversation.id,
                "conversationTitle": self.conversation.title,
                "titleSource": self.conversation.title_source,
            },
            session_id=self.session_id,
        )

    async def _delete_session(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message) or not await self._can_change_conversation():
            return
        conversation_id = message["payload"].get("conversationId")
        if not isinstance(conversation_id, str) or not conversation_id:
            await self._error("invalid_message", "payload.conversationId must be a string")
            return
        assert self.session_store is not None and self.conversation is not None
        try:
            await asyncio.to_thread(self.session_store.delete, conversation_id)
        except ValueError as exc:
            await self._error("unknown_conversation", str(exc))
            return

        active_changed = self.conversation.id == conversation_id
        if active_changed:
            self.conversation = await asyncio.to_thread(self.session_store.latest_or_create)
            self.agent = self._build_agent(self.conversation)
        sessions = await asyncio.to_thread(self.session_store.list)
        await self.emitter.emit(
            "conversation_deleted",
            {
                "deletedConversationId": conversation_id,
                "activeConversationId": self.conversation.id,
                "conversationTitle": self.conversation.title,
                "titleSource": self.conversation.title_source,
                "transcript": self.session_store.transcript(self.conversation.messages)
                if active_changed
                else None,
                "activeChanged": active_changed,
                "sessions": sessions,
            },
            session_id=self.session_id,
        )

    async def _can_change_conversation(self) -> bool:
        if self.active_task and not self.active_task.done():
            await self._error("turn_already_running", "cannot change sessions while a turn is running")
            return False
        return True

    async def _emit_conversation(self, event_type: str) -> None:
        assert self.session_store is not None and self.conversation is not None
        await self.emitter.emit(
            event_type,
            {
                "conversationId": self.conversation.id,
                "conversationTitle": self.conversation.title,
                "titleSource": self.conversation.title_source,
                "transcript": self.session_store.transcript(self.conversation.messages),
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
        self.turn_resume_event.set()
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

    async def _request_user_input(self, tool_call_id: str, question: str) -> str | None:
        if self.session_id is None or self.active_turn_id is None:
            return None
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self.questions[tool_call_id] = future
        await self.emitter.emit(
            "user_input_required",
            {"question": question},
            session_id=self.session_id,
            turn_id=self.active_turn_id,
            tool_call_id=tool_call_id,
        )
        try:
            return await future
        finally:
            self.questions.pop(tool_call_id, None)

    async def _user_input_response(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if message.get("turnId") != self.active_turn_id:
            await self._error("unknown_turn", "turnId is not active")
            return
        tool_call_id = message.get("toolCallId")
        future = self.questions.get(tool_call_id)
        if future is None or future.done():
            await self._error("user_input_not_pending", "tool call is not waiting for user input")
            return
        payload = message["payload"]
        cancelled = payload.get("cancelled", False)
        answer = payload.get("answer")
        if not isinstance(cancelled, bool):
            await self._error("invalid_message", "cancelled must be a boolean")
            return
        if cancelled:
            future.set_result(None)
            return
        if not isinstance(answer, str) or not answer.strip():
            await self._error("invalid_message", "answer must be a non-empty string")
            return
        if len(answer) > 10_000:
            await self._error("invalid_message", "answer must not exceed 10000 characters")
            return
        future.set_result(answer.strip())

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
        self.turn_resume_event.set()
        if not self.active_task.done():
            self.active_task.cancel()

    async def _pause_turn(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if message.get("turnId") != self.active_turn_id or self.active_task is None:
            await self._error("unknown_turn", "turnId is not active")
            return
        if self.active_task.done() or not self.turn_resume_event.is_set():
            return
        self.turn_resume_event.clear()
        await self.emitter.emit(
            "turn_paused",
            {},
            session_id=self.session_id,
            turn_id=self.active_turn_id,
        )

    async def _resume_turn(self, message: dict[str, Any]) -> None:
        if not await self._check_session(message):
            return
        if message.get("turnId") != self.active_turn_id or self.active_task is None:
            await self._error("unknown_turn", "turnId is not active")
            return
        if self.active_task.done() or self.turn_resume_event.is_set():
            return
        self.turn_resume_event.set()
        await self.emitter.emit(
            "turn_resumed",
            {},
            session_id=self.session_id,
            turn_id=self.active_turn_id,
        )

    async def _wait_until_resumed(self) -> None:
        await self.turn_resume_event.wait()

    async def _shutdown(self, *, emit_complete: bool) -> None:
        self.shutting_down = True
        self.turn_resume_event.set()
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
        for future in self.approvals.values():
            if not future.done():
                future.cancel()
        self.approvals.clear()
        for future in self.questions.values():
            if not future.done():
                future.cancel()
        self.questions.clear()
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
        self.turn_resume_event.set()

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
