from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, RateLimitError

from agent_coder.model import ContextLengthError, OpenAICompatibleClient


class FakeCompletions:
    def __init__(self, chunks, *, reject_usage: bool = False) -> None:
        self.chunks = chunks
        self.reject_usage = reject_usage
        self.arguments: list[dict] = []

    async def create(self, **arguments):
        self.arguments.append(arguments)
        if self.reject_usage and "stream_options" in arguments:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(400, request=request)
            raise BadRequestError(
                "stream_options include_usage is not supported", response=response, body=None
            )

        async def stream():
            for chunk in self.chunks:
                yield chunk

        return stream()


@pytest.mark.asyncio
async def test_streaming_client_collects_usage_only_chunk() -> None:
    text_chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="Done", tool_calls=None),
            )
        ],
    )
    usage_chunk = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=8,
            total_tokens=128,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
        choices=[],
    )
    completions = FakeCompletions([text_chunk, usage_chunk])
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    reply = await client.complete([], [], on_delta)

    assert completions.arguments[0]["stream_options"] == {"include_usage": True}
    assert reply.content == "Done"
    assert reply.usage is not None
    assert reply.usage.as_dict() == {
        "promptTokens": 120,
        "completionTokens": 8,
        "totalTokens": 128,
        "cachedTokens": 40,
        "reasoningTokens": 3,
    }
    assert deltas == ["Done"]


@pytest.mark.asyncio
async def test_streaming_client_collects_reasoning_content() -> None:
    reasoning_chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content="",
                    tool_calls=None,
                    reasoning_content="Let me inspect ",
                )
            )
        ],
    )
    text_chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="Done", tool_calls=None)
            )
        ],
    )
    completions = FakeCompletions([reasoning_chunk, text_chunk])
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    reasoning_deltas: list[str] = []

    async def on_reasoning_delta(text: str) -> None:
        reasoning_deltas.append(text)

    async def on_delta(text: str) -> None:
        del text

    reply = await client.complete([], [], on_delta, on_reasoning_delta)

    assert reply.reasoning == "Let me inspect "
    assert reasoning_deltas == ["Let me inspect "]


@pytest.mark.asyncio
async def test_client_falls_back_when_provider_rejects_usage_option() -> None:
    chunk = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content="Done", tool_calls=None))],
    )
    completions = FakeCompletions([chunk], reject_usage=True)
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    async def on_delta(text: str) -> None:
        del text

    reply = await client.complete([], [], on_delta)

    assert len(completions.arguments) == 2
    assert "stream_options" not in completions.arguments[1]
    assert reply.usage is None


@pytest.mark.asyncio
async def test_summarize_returns_parsed_dict() -> None:
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )

    async def fake_create(**arguments):
        del arguments
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"goal": "fix parser", "constraints": ["no deps"]}')
                )
            ]
        )

    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    result = await client.summarize([{"role": "user", "content": "hi"}])

    assert result == {"goal": "fix parser", "constraints": ["no deps"]}


@pytest.mark.asyncio
async def test_summarize_returns_none_on_invalid_json() -> None:
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )

    async def fake_create(**arguments):
        del arguments
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not a json object"))]
        )

    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    assert await client.summarize([{"role": "user", "content": "hi"}]) is None


@pytest.mark.asyncio
async def test_client_retries_rate_limit_then_succeeds(monkeypatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        del _seconds

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )
    attempts = {"count": 0}
    text_chunk = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content="Done", tool_calls=None))],
    )

    async def fake_create(**arguments):
        del arguments
        attempts["count"] += 1
        if attempts["count"] == 1:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(429, request=request)
            raise RateLimitError("rate limited", response=response, body=None)

        async def stream():
            yield text_chunk

        return stream()

    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    async def on_delta(text: str) -> None:
        del text

    reply = await client.complete([], [], on_delta)

    assert attempts["count"] == 2
    assert reply.content == "Done"


@pytest.mark.asyncio
async def test_client_does_not_retry_authentication() -> None:
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )
    attempts = {"count": 0}

    async def fake_create(**arguments):
        del arguments
        attempts["count"] += 1
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        response = httpx.Response(401, request=request)
        raise AuthenticationError("invalid key", response=response, body=None)

    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    async def on_delta(text: str) -> None:
        del text

    with pytest.raises(AuthenticationError):
        await client.complete([], [], on_delta)

    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_client_raises_context_length_error() -> None:
    client = OpenAICompatibleClient(
        api_key="unused", base_url="https://example.invalid/v1", model="test-model"
    )

    async def fake_create(**arguments):
        del arguments
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        response = httpx.Response(400, request=request)
        raise BadRequestError(
            "This model's maximum context length is 4096 tokens",
            response=response,
            body=None,
        )

    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    async def on_delta(text: str) -> None:
        del text

    with pytest.raises(ContextLengthError):
        await client.complete([], [], on_delta)
