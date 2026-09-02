from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from agent_coder.model import OpenAICompatibleClient


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
