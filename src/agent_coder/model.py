from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI, BadRequestError


TextDeltaCallback = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None

    def as_dict(self) -> dict[str, int]:
        result = {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
        }
        if self.cached_tokens is not None:
            result["cachedTokens"] = self.cached_tokens
        if self.reasoning_tokens is not None:
            result["reasoningTokens"] = self.reasoning_tokens
        return result


@dataclass(slots=True)
class AssistantReply:
    content: str
    tool_calls: list[ToolCall]
    usage: TokenUsage | None = None

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
        request: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            stream = await self._client.chat.completions.create(**request)
        except BadRequestError as exc:
            detail = str(exc).lower()
            if "stream_options" not in detail and "include_usage" not in detail:
                raise
            request.pop("stream_options")
            stream = await self._client.chat.completions.create(**request)

        text_parts: list[str] = []
        accumulated: dict[int, dict[str, str]] = {}
        usage: TokenUsage | None = None

        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                prompt_details = getattr(chunk_usage, "prompt_tokens_details", None)
                completion_details = getattr(chunk_usage, "completion_tokens_details", None)
                usage = TokenUsage(
                    prompt_tokens=chunk_usage.prompt_tokens,
                    completion_tokens=chunk_usage.completion_tokens,
                    total_tokens=chunk_usage.total_tokens,
                    cached_tokens=(
                        getattr(prompt_details, "cached_tokens", None)
                        if prompt_details is not None
                        else None
                    ),
                    reasoning_tokens=(
                        getattr(completion_details, "reasoning_tokens", None)
                        if completion_details is not None
                        else None
                    ),
                )
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

        return AssistantReply(content="".join(text_parts), tool_calls=calls, usage=usage)
