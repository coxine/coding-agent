from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any, TextIO


PROTOCOL_VERSION = 1


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ProtocolEmitter:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._lock = asyncio.Lock()

    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        message: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "type": event_type,
            "messageId": new_id("msg"),
            "timestamp": utc_timestamp(),
            "payload": payload or {},
        }
        if session_id is not None:
            message["sessionId"] = session_id
        if turn_id is not None:
            message["turnId"] = turn_id
        if tool_call_id is not None:
            message["toolCallId"] = tool_call_id

        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            self._stream.write(encoded + "\n")
            self._stream.flush()


def validate_envelope(message: Any) -> str | None:
    if not isinstance(message, dict):
        return "message must be a JSON object"
    if message.get("protocolVersion") != PROTOCOL_VERSION:
        return "unsupported protocolVersion"
    if not isinstance(message.get("type"), str):
        return "type must be a string"
    if not isinstance(message.get("messageId"), str):
        return "messageId must be a string"
    if not isinstance(message.get("payload"), dict):
        return "payload must be an object"
    return None

