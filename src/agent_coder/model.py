from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)


TextDeltaCallback = Callable[[str], Awaitable[None]]

_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_RETRYABLE_EXCEPTIONS = (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)


class ContextLengthError(Exception):
    """Raised when a request exceeds the model's context window."""


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
    reasoning: str = ""
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
        on_reasoning_delta: TextDeltaCallback | None = None,
    ) -> AssistantReply: ...

    async def summarize(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None: ...


def _parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_context_length_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "context length",
            "context_length",
            "maximum context",
            "too many tokens",
            "reduce the length",
        )
    )


class OpenAICompatibleClient:
    """Small Chat Completions adapter; orchestration remains in Agent."""

    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=60.0,
        )
        self._model = model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaCallback,
        on_reasoning_delta: TextDeltaCallback | None = None,
    ) -> AssistantReply:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            emitted: list[bool] = [False]
            try:
                return await self._stream_once(
                    request, on_text_delta, on_reasoning_delta, emitted
                )
            except (
                ContextLengthError,
                AuthenticationError,
                PermissionDeniedError,
                BadRequestError,
            ):
                raise
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                if emitted[0] or attempt == _MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_BACKOFF_SECONDS[attempt] + random.uniform(0.0, 0.5))
        raise last_error  # pragma: no cover

    async def _stream_once(
        self,
        request: dict[str, Any],
        on_text_delta: TextDeltaCallback,
        on_reasoning_delta: TextDeltaCallback | None,
        emitted: list[bool],
    ) -> AssistantReply:
        try:
            stream = await self._client.chat.completions.create(**request)
        except BadRequestError as exc:
            detail = str(exc).lower()
            if "stream_options" in detail or "include_usage" in detail:
                request.pop("stream_options", None)
                stream = await self._client.chat.completions.create(**request)
            elif _is_context_length_error(exc):
                raise ContextLengthError(str(exc)) from exc
            else:
                raise

        async def text_delta(text: str) -> None:
            emitted[0] = True
            await on_text_delta(text)

        async def reasoning_delta(text: str) -> None:
            emitted[0] = True
            if on_reasoning_delta is not None:
                await on_reasoning_delta(text)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
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
            reasoning_content = getattr(delta, "reasoning_content", None)
            if not isinstance(reasoning_content, str):
                reasoning_content = getattr(delta, "reasoning", None)
            if isinstance(reasoning_content, str) and reasoning_content:
                reasoning_parts.append(reasoning_content)
                await reasoning_delta(reasoning_content)
            if delta.content:
                text_parts.append(delta.content)
                await text_delta(delta.content)

            for call_delta in delta.tool_calls or []:
                emitted[0] = True
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

        return AssistantReply(
            content="".join(text_parts),
            tool_calls=calls,
            reasoning="".join(reasoning_parts),
            usage=usage,
        )

    async def summarize(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=False,
            )
        except Exception:
            return None
        if not response.choices:
            return None
        content = response.choices[0].message.content
        if not isinstance(content, str):
            return None
        return _parse_json_object(content)
