from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .protocol import new_id


SESSION_VERSION = 1
_ID_PATTERN = re.compile(r"^conv_[0-9a-f]{32}$")
_ALLOWED_ROLES = {"user", "assistant", "tool"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]]
    title_source: str = "auto"
    usage_records: list[dict[str, Any]] = field(default_factory=list)
    compact_state: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messageCount": sum(1 for message in self.messages if message.get("role") == "user"),
            "titleSource": self.title_source,
        }


class SessionStore:
    def __init__(self, workspace_root: Path) -> None:
        self.directory = workspace_root / ".coding-agent" / "sessions"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory.parent, 0o700)
        os.chmod(self.directory, 0o700)

    def create(self) -> Conversation:
        timestamp = _now()
        conversation = Conversation(
            id=new_id("conv"),
            title="New session",
            created_at=timestamp,
            updated_at=timestamp,
            messages=[],
        )
        self.save(conversation)
        return conversation

    def latest_or_create(self) -> Conversation:
        conversations = self.list()
        return self.load(conversations[0]["id"]) if conversations else self.create()

    def list(self) -> list[dict[str, Any]]:
        conversations: list[Conversation] = []
        for path in self.directory.glob("conv_*.json"):
            try:
                conversations.append(self._read(path))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        conversations.sort(key=lambda item: item.updated_at, reverse=True)
        return [conversation.summary() for conversation in conversations]

    def load(self, conversation_id: str) -> Conversation:
        if not _ID_PATTERN.fullmatch(conversation_id):
            raise ValueError("invalid conversation id")
        path = self.directory / f"{conversation_id}.json"
        if not path.is_file():
            raise ValueError("conversation does not exist")
        return self._read(path)

    def delete(self, conversation_id: str) -> None:
        if not _ID_PATTERN.fullmatch(conversation_id):
            raise ValueError("invalid conversation id")
        path = self.directory / f"{conversation_id}.json"
        if not path.is_file():
            raise ValueError("conversation does not exist")
        path.unlink()

    def update_messages(
        self,
        conversation: Conversation,
        messages: list[dict[str, Any]],
        compact_state: dict[str, Any] | None = None,
    ) -> Conversation:
        conversation.messages = deepcopy(messages)
        if compact_state is not None:
            conversation.compact_state = deepcopy(compact_state)
        conversation.updated_at = _now()
        if conversation.title_source == "auto":
            conversation.title = self._title(messages)
        self.save(conversation)
        return conversation

    def rename(self, conversation: Conversation, title: str) -> Conversation:
        normalized = self._normalize_title(title)
        if not normalized:
            raise ValueError("session name must not be empty")
        if len(normalized) > 80:
            raise ValueError("session name must not exceed 80 characters")
        conversation.title = normalized
        conversation.title_source = "custom"
        conversation.updated_at = _now()
        self.save(conversation)
        return conversation

    def record_usage(
        self,
        conversation: Conversation,
        *,
        turn_id: str,
        step: int,
        usage: dict[str, int] | None,
    ) -> None:
        record: dict[str, Any] = {
            "recordedAt": _now(),
            "turnId": turn_id,
            "step": step,
            "available": usage is not None,
        }
        if usage is not None:
            record.update(usage)
        conversation.usage_records.append(record)
        conversation.updated_at = record["recordedAt"]
        self.save(conversation)

    @staticmethod
    def usage_summary(conversation: Conversation) -> dict[str, Any]:
        available = [record for record in conversation.usage_records if record["available"]]
        return {
            "requestCount": len(conversation.usage_records),
            "measuredRequests": len(available),
            "unavailableRequests": len(conversation.usage_records) - len(available),
            "latest": deepcopy(conversation.usage_records[-1])
            if conversation.usage_records
            else None,
            "latestMeasured": deepcopy(available[-1]) if available else None,
            "totals": {
                key: sum(int(record.get(key, 0)) for record in available)
                for key in (
                    "promptTokens",
                    "completionTokens",
                    "totalTokens",
                    "cachedTokens",
                    "reasoningTokens",
                )
            },
        }

    def save(self, conversation: Conversation) -> None:
        if not _ID_PATTERN.fullmatch(conversation.id):
            raise ValueError("invalid conversation id")
        payload = {
            "version": SESSION_VERSION,
            "id": conversation.id,
            "title": conversation.title,
            "createdAt": conversation.created_at,
            "updatedAt": conversation.updated_at,
            "titleSource": conversation.title_source,
            "messages": conversation.messages,
            "usage": conversation.usage_records,
            "compactState": conversation.compact_state,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{conversation.id}.", suffix=".tmp", dir=self.directory
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            target = self.directory / f"{conversation.id}.json"
            os.replace(temporary_name, target)
            os.chmod(target, 0o600)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def transcript(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                result.append({"role": role, "content": content})
        return result

    def _read(self, path: Path) -> Conversation:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != SESSION_VERSION:
            raise ValueError("unsupported session file")
        conversation_id = data.get("id")
        if not isinstance(conversation_id, str) or not _ID_PATTERN.fullmatch(conversation_id):
            raise ValueError("invalid conversation id")
        if path.name != f"{conversation_id}.json":
            raise ValueError("conversation id does not match filename")
        messages = data.get("messages")
        if not isinstance(messages, list) or not all(self._valid_message(item) for item in messages):
            raise ValueError("invalid conversation messages")
        title = data.get("title")
        created_at = data.get("createdAt")
        updated_at = data.get("updatedAt")
        title_source = data.get("titleSource", "auto")
        usage_records = data.get("usage", [])
        compact_state = data.get("compactState", {})
        if not all(isinstance(value, str) and value for value in (title, created_at, updated_at)):
            raise ValueError("invalid conversation metadata")
        if title_source not in {"auto", "custom"}:
            raise ValueError("invalid conversation title source")
        if not isinstance(usage_records, list) or not all(
            SessionStore._valid_usage_record(record) for record in usage_records
        ):
            raise ValueError("invalid conversation usage records")
        if not isinstance(compact_state, dict):
            raise ValueError("invalid conversation compact state")
        return Conversation(
            conversation_id,
            title,
            created_at,
            updated_at,
            deepcopy(messages),
            title_source,
            deepcopy(usage_records),
            deepcopy(compact_state),
        )

    @staticmethod
    def _valid_message(message: Any) -> bool:
        return (
            isinstance(message, dict)
            and message.get("role") in _ALLOWED_ROLES
            and (message.get("content") is None or isinstance(message.get("content"), str))
        )

    @staticmethod
    def _valid_usage_record(record: Any) -> bool:
        if not isinstance(record, dict):
            return False
        if not isinstance(record.get("recordedAt"), str):
            return False
        if not isinstance(record.get("turnId"), str):
            return False
        if isinstance(record.get("step"), bool) or not isinstance(record.get("step"), int):
            return False
        if not isinstance(record.get("available"), bool):
            return False
        token_keys = {
            "promptTokens",
            "completionTokens",
            "totalTokens",
            "cachedTokens",
            "reasoningTokens",
        }
        if record["available"] and not {
            "promptTokens",
            "completionTokens",
            "totalTokens",
        }.issubset(record):
            return False
        return all(
            not isinstance(record.get(key), bool)
            and isinstance(record.get(key), int)
            and record[key] >= 0
            for key in token_keys.intersection(record)
        )

    @staticmethod
    def _title(messages: list[dict[str, Any]]) -> str:
        for message in messages:
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                normalized = SessionStore._normalize_title(message["content"])
                if normalized:
                    return normalized[:57] + "..." if len(normalized) > 60 else normalized
        return "New session"

    @staticmethod
    def _normalize_title(value: str) -> str:
        printable = "".join(
            character for character in value if character.isprintable() or character.isspace()
        )
        return " ".join(printable.split())
