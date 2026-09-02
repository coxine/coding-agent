from __future__ import annotations

import asyncio
import io
import json

import pytest

from agent_coder.protocol import ProtocolEmitter
from agent_coder.server import CoreServer


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
