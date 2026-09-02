from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from agent_coder.config import AgentConfig
from agent_coder.protocol import ProtocolEmitter
from agent_coder.server import CoreServer
from agent_coder.sessions import Conversation, SessionStore


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
async def test_core_can_pause_and_resume_active_turn() -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"
    server.active_turn_id = "turn_test"
    server.active_task = asyncio.create_task(asyncio.Event().wait())
    message = {"sessionId": "sess_test", "turnId": "turn_test", "payload": {}}

    try:
        await server._pause_turn(message)
        waiter = asyncio.create_task(server._wait_until_resumed())
        await asyncio.sleep(0)
        assert not server.turn_resume_event.is_set()
        assert not waiter.done()

        await server._resume_turn(message)
        await waiter
        assert server.turn_resume_event.is_set()
        assert [event["type"] for event in map(json.loads, output.getvalue().splitlines())] == [
            "turn_paused",
            "turn_resumed",
        ]
    finally:
        server.active_task.cancel()
        await asyncio.gather(server.active_task, return_exceptions=True)


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
        context_window_tokens=128000,
    )
    server.conversation = Conversation(
        id="conv_00000000000000000000000000000000",
        title="Status test",
        created_at="2026-09-02T00:00:00Z",
        updated_at="2026-09-02T01:00:00Z",
        messages=[{"role": "user", "content": "hello"}],
    )
    server.session_store = SessionStore(tmp_path)
    server.session_store.save(server.conversation)
    server.session_store.record_usage(
        server.conversation,
        turn_id="turn_1",
        step=1,
        usage={"promptTokens": 100, "completionTokens": 10, "totalTokens": 110},
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
    assert event["payload"]["tokenUsage"]["latest"]["promptTokens"] == 100
    assert event["payload"]["tokenUsage"]["contextWindowTokens"] == 128000
    assert event["payload"]["metadata"]["tools"] == 2
    assert "userTurns" not in event["payload"]["metadata"]
    assert "persistedMessages" not in event["payload"]["metadata"]
    assert "titleSource" not in event["payload"]["metadata"]


@pytest.mark.asyncio
async def test_core_can_rename_current_session(tmp_path) -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"
    server.session_store = SessionStore(tmp_path)
    server.conversation = server.session_store.create()

    await server._rename_session(
        {"sessionId": "sess_test", "payload": {"name": "Parser cleanup"}}
    )

    event = json.loads(output.getvalue().splitlines()[0])
    assert event["type"] == "conversation_updated"
    assert event["payload"]["conversationTitle"] == "Parser cleanup"
    assert event["payload"]["titleSource"] == "custom"
    assert server.session_store.load(server.conversation.id).title == "Parser cleanup"


@pytest.mark.asyncio
async def test_core_can_delete_non_active_session(tmp_path) -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"
    server.session_store = SessionStore(tmp_path)
    server.conversation = server.session_store.create()
    other = server.session_store.create()

    await server._delete_session(
        {"sessionId": "sess_test", "payload": {"conversationId": other.id}}
    )

    event = json.loads(output.getvalue().splitlines()[0])
    assert event["type"] == "conversation_deleted"
    assert event["payload"]["deletedConversationId"] == other.id
    assert event["payload"]["activeChanged"] is False
    assert event["payload"]["activeConversationId"] == server.conversation.id
    assert [session["id"] for session in event["payload"]["sessions"]] == [
        server.conversation.id
    ]


@pytest.mark.asyncio
async def test_core_deleting_active_session_switches_to_latest(tmp_path) -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"
    server.config = AgentConfig(
        workspace_root=tmp_path,
        api_key="unused",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    server.session_store = SessionStore(tmp_path)
    server.conversation = server.session_store.create()
    other = server.session_store.create()
    server.session_store.update_messages(other, [{"role": "user", "content": "keep me"}])

    await server._delete_session(
        {"sessionId": "sess_test", "payload": {"conversationId": server.conversation.id}}
    )

    event = json.loads(output.getvalue().splitlines()[0])
    assert event["type"] == "conversation_deleted"
    assert event["payload"]["activeChanged"] is True
    assert event["payload"]["activeConversationId"] == other.id
    assert server.conversation.id == other.id
    assert event["payload"]["transcript"][0]["content"] == "keep me"


@pytest.mark.asyncio
async def test_run_processes_lines_until_shutdown() -> None:
    output = io.StringIO()
    stream = io.StringIO(
        "not json\n"
        + json.dumps(
            {
                "protocolVersion": 1,
                "type": "shutdown",
                "messageId": "msg_1",
                "timestamp": "2026-09-02T00:00:00Z",
                "sessionId": "sess_x",
                "payload": {},
            }
        )
        + "\n"
    )
    server = CoreServer(input_stream=stream, emitter=ProtocolEmitter(output))
    server.session_id = "sess_x"

    code = await server.run()

    assert code == 0
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events[0]["type"] == "error"
    assert events[0]["payload"]["code"] == "invalid_json"
    assert events[-1]["type"] == "shutdown_complete"


@pytest.mark.asyncio
async def test_core_can_compact_context_manually() -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"

    async def compact_now() -> dict:
        return {
            "summarized": True,
            "messageCountBefore": 20,
            "messageCountAfter": 12,
            "stateCounts": {"goal": True},
        }

    server.agent = SimpleNamespace(compact_now=compact_now)

    await server._compact_context({"sessionId": "sess_test", "payload": {}})

    event = json.loads(output.getvalue().splitlines()[0])
    assert event["type"] == "context_compacted"
    assert event["payload"]["summarized"] is True
    assert event["payload"]["messageCountAfter"] == 12


@pytest.mark.asyncio
async def test_core_rejects_compact_while_turn_running() -> None:
    output = io.StringIO()
    server = CoreServer(emitter=ProtocolEmitter(output))
    server.session_id = "sess_test"

    async def compact_now() -> dict:
        return {}

    server.agent = SimpleNamespace(compact_now=compact_now)
    server.active_task = asyncio.create_task(asyncio.Event().wait())
    try:
        await server._compact_context({"sessionId": "sess_test", "payload": {}})
        event = json.loads(output.getvalue().splitlines()[0])
        assert event["type"] == "error"
        assert event["payload"]["code"] == "turn_already_running"
    finally:
        server.active_task.cancel()
        await asyncio.gather(server.active_task, return_exceptions=True)
