from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from agent_coder.config import AgentConfig
from agent_coder.protocol import ProtocolEmitter
from agent_coder.server import CoreServer
from agent_coder.sessions import Conversation


@pytest.mark.asyncio
async def test_core_user_input_round_trip() -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"
    server.active_turn_id = "turn_test"

    pending = asyncio.create_task(server._request_user_input("call_1", "Which format?"))
    await asyncio.sleep(0)
    event = json.loads(output.getvalue().splitlines()[0])
    assert event["type"] == "user_input_required"
    assert event["payload"]["question"] == "Which format?"

    await server._user_input_response(
        {
            "sessionId": "sess_test",
            "turnId": "turn_test",
            "toolCallId": "call_1",
            "payload": {"answer": "JSON"},
        }
    )
    assert await pending == "JSON"
    assert server.questions == {}


@pytest.mark.asyncio
async def test_core_user_can_cancel_question() -> None:
    server = CoreServer(emitter=ProtocolEmitter(io.StringIO()))
    server.session_id = "sess_test"
    server.active_turn_id = "turn_test"
    pending = asyncio.create_task(server._request_user_input("call_1", "Continue?"))
    await asyncio.sleep(0)
    await server._user_input_response(
        {
            "sessionId": "sess_test",
            "turnId": "turn_test",
            "toolCallId": "call_1",
            "payload": {"cancelled": True},
        }
    )
    assert await pending is None


@pytest.mark.asyncio
async def test_core_status_report_contains_context_and_metadata(tmp_path) -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"
    server.config = AgentConfig(
        workspace_root=tmp_path,
        api_key="unused",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    server.conversation = Conversation(
        id="conv_00000000000000000000000000000000",
        title="Status test",
        created_at="2026-09-02T00:00:00Z",
        updated_at="2026-09-02T01:00:00Z",
        messages=[{"role": "user", "content": "hello"}],
    )
    server.agent = SimpleNamespace(
        context_stats=lambda: {
            "totalChars": 1000,
            "requestChars": 800,
            "maxChars": 200000,
            "messageCount": 2,
            "requestMessageCount": 2,
            "truncated": False,
        },
        tools=SimpleNamespace(schemas=[{}, {}]),
    )

    await server._get_status({"sessionId": "sess_test", "payload": {}})

    event = json.loads(output.getvalue().splitlines()[0])
    assert event["type"] == "status_report"
    assert event["payload"]["model"] == "test-model"
    assert event["payload"]["context"]["requestChars"] == 800
    assert event["payload"]["metadata"]["tools"] == 2
    assert event["payload"]["metadata"]["userTurns"] == 1
