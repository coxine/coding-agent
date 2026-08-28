from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI


TextDeltaCallback = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class AssistantReply:
    content: str
    tool_calls: list[ToolCall]

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class ModelClientProtocol(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaCallback,
    ) -> AssistantReply: ...


class OpenAICompatibleClient:
    """Small Chat Completions adapter; orchestration remains in Agent."""

    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=2,
            timeout=60.0,
        )
        self._model = model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaCallback,
    ) -> AssistantReply:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            tool_choice="auto",
            stream=True,
        )

        text_parts: list[str] = []
        accumulated: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text_parts.append(delta.content)
                await on_text_delta(delta.content)

            for call_delta in delta.tool_calls or []:
                index = call_delta.index
                current = accumulated.setdefault(
                    index, {"id": "", "name": "", "arguments": ""}
                )
                if call_delta.id:
                    current["id"] += call_delta.id
                if call_delta.function:
                    if call_delta.function.name:
                        current["name"] += call_delta.function.name
                    if call_delta.function.arguments:
                        current["arguments"] += call_delta.function.arguments

        calls: list[ToolCall] = []
        for index in sorted(accumulated):
            item = accumulated[index]
            call_id = item["id"] or f"call_{index}"
            name = item["name"]
            arguments = item["arguments"] or "{}"
            # Parse only to detect invalid JSON early; Agent returns a tool error.
            try:
                parsed = json.loads(arguments)
                if not isinstance(parsed, dict):
                    raise ValueError("tool arguments must be an object")
            except (json.JSONDecodeError, ValueError):
                pass
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))

        return AssistantReply(content="".join(text_parts), tool_calls=calls)

