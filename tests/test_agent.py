from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

from agent_coder.agent import Agent
from agent_coder.config import AgentConfig
from agent_coder.model import AssistantReply, ToolCall
from agent_coder.protocol import ProtocolEmitter
from agent_coder.tools import ToolRegistry


class FakeModel:
    def __init__(self, replies: list[AssistantReply]) -> None:
        self.replies = replies
        self.requests: list[list[dict[str, Any]]] = []

    async def complete(self, messages, tools, on_text_delta):
        del tools
        self.requests.append(messages)
        reply = self.replies.pop(0)
        if reply.content:
            await on_text_delta(reply.content)
        return reply


def config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        workspace_root=tmp_path,
        api_key="unused",
        base_url="https://example.invalid/v1",
        model="fake-model",
        max_steps=10,
    )


def events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.mark.asyncio
async def test_agent_finishes_without_tools(tmp_path) -> None:
    output = io.StringIO()
    model = FakeModel([AssistantReply(content="Done.", tool_calls=[])])

    async def approve(tool_call_id: str, payload: dict[str, Any]) -> bool:
        del tool_call_id, payload
        return False

    agent = Agent(
        config=config(tmp_path),
        session_id="sess_test",
        emitter=ProtocolEmitter(output),
        model=model,
        tools=ToolRegistry(tmp_path),
        request_approval=approve,
    )
    await agent.run_turn("turn_test", "Say done")

    names = [event["type"] for event in events(output)]
    assert names[-1] == "turn_finished"
    assert names.count("turn_finished") == 1


@pytest.mark.asyncio
async def test_agent_executes_tool_and_returns_result(tmp_path) -> None:
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    output = io.StringIO()
    model = FakeModel(
        [
            AssistantReply(
                content="I will inspect the file.",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments='{"path":"hello.txt"}',
                    )
                ],
            ),
            AssistantReply(content="The file contains hello.", tool_calls=[]),
        ]
    )

    async def approve(tool_call_id: str, payload: dict[str, Any]) -> bool:
        del tool_call_id, payload
        return True

    agent = Agent(
        config=config(tmp_path),
        session_id="sess_test",
        emitter=ProtocolEmitter(output),
        model=model,
        tools=ToolRegistry(tmp_path),
        request_approval=approve,
    )
    await agent.run_turn("turn_test", "Read hello.txt")

    names = [event["type"] for event in events(output)]
    assert "tool_requested" in names
    assert "tool_started" in names
    assert "tool_finished" in names
    assert names[-1] == "turn_finished"
    tool_messages = [message for message in model.requests[1] if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert json.loads(tool_messages[0]["content"])["ok"] is True


@pytest.mark.asyncio
async def test_high_risk_tool_can_be_denied(tmp_path) -> None:
    output = io.StringIO()
    model = FakeModel(
        [
            AssistantReply(
                content="",
                tool_calls=[
                    ToolCall(id="call_1", name="run_command", arguments='{"command":"npm install"}')
                ],
            ),
            AssistantReply(content="The install was not performed.", tool_calls=[]),
        ]
    )

    async def deny(tool_call_id: str, payload: dict[str, Any]) -> bool:
        assert tool_call_id == "call_1"
        assert payload["name"] == "run_command"
        return False

    agent = Agent(
        config=config(tmp_path),
        session_id="sess_test",
        emitter=ProtocolEmitter(output),
        model=model,
        tools=ToolRegistry(tmp_path),
        request_approval=deny,
    )
    await agent.run_turn("turn_test", "Install dependencies")

    tool_event = next(event for event in events(output) if event["type"] == "tool_finished")
    assert tool_event["payload"]["error"]["code"] == "approval_denied"
    assert events(output)[-1]["type"] == "turn_finished"


@pytest.mark.asyncio
async def test_agent_restores_history_and_persists_completed_turn(tmp_path) -> None:
    output = io.StringIO()
    model = FakeModel([AssistantReply(content="Second answer.", tool_calls=[])])
    snapshots: list[list[dict[str, Any]]] = []

    async def approve(tool_call_id: str, payload: dict[str, Any]) -> bool:
        del tool_call_id, payload
        return False

    async def persist(messages: list[dict[str, Any]]) -> None:
        snapshots.append([dict(message) for message in messages])

    history = [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer."},
    ]
    agent = Agent(
        config=config(tmp_path),
        session_id="sess_test",
        emitter=ProtocolEmitter(output),
        model=model,
        tools=ToolRegistry(tmp_path),
        request_approval=approve,
        history_messages=history,
        persist_messages=persist,
    )
    await agent.run_turn("turn_test", "Second question")

    assert model.requests[0][1:3] == history
    assert snapshots[0][-1] == {"role": "user", "content": "Second question"}
    assert snapshots[-1][-1] == {"role": "assistant", "content": "Second answer."}


@pytest.mark.asyncio
async def test_agent_can_pause_for_user_input_and_continue(tmp_path) -> None:
    output = io.StringIO()
    model = FakeModel(
        [
            AssistantReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_question",
                        name="request_user_input",
                        arguments='{"question":"Which format should I use?"}',
                    )
                ],
            ),
            AssistantReply(content="I will use JSON.", tool_calls=[]),
        ]
    )

    async def approve(tool_call_id: str, payload: dict[str, Any]) -> bool:
        del tool_call_id, payload
        return False

    async def answer(tool_call_id: str, question: str) -> str | None:
        assert tool_call_id == "call_question"
        assert question == "Which format should I use?"
        return "JSON"

    agent = Agent(
        config=config(tmp_path),
        session_id="sess_test",
        emitter=ProtocolEmitter(output),
        model=model,
        tools=ToolRegistry(tmp_path),
        request_approval=approve,
        request_user_input=answer,
    )
    await agent.run_turn("turn_test", "Create an export")

    tool_message = next(message for message in model.requests[1] if message["role"] == "tool")
    assert json.loads(tool_message["content"])["data"]["answer"] == "JSON"
    assert events(output)[-1]["type"] == "turn_finished"


@pytest.mark.asyncio
async def test_cancelling_user_question_closes_tool_call_in_history(tmp_path) -> None:
    output = io.StringIO()
    model = FakeModel(
        [
            AssistantReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_question",
                        name="request_user_input",
                        arguments='{"question":"Which format should I use?"}',
                    )
                ],
            )
        ]
    )
    question_started = asyncio.Event()

    async def approve(tool_call_id: str, payload: dict[str, Any]) -> bool:
        del tool_call_id, payload
        return False

    async def wait_for_answer(tool_call_id: str, question: str) -> str | None:
        del tool_call_id, question
        question_started.set()
        await asyncio.Future()
        return None

    agent = Agent(
        config=config(tmp_path),
        session_id="sess_test",
        emitter=ProtocolEmitter(output),
        model=model,
        tools=ToolRegistry(tmp_path),
        request_approval=approve,
        request_user_input=wait_for_answer,
    )
    task = asyncio.create_task(agent.run_turn("turn_test", "Create an export"))
    await question_started.wait()
    task.cancel()
    await task

    tool_message = next(message for message in agent.messages if message["role"] == "tool")
    assert json.loads(tool_message["content"])["error"]["code"] == "cancelled"
    assert any(event["type"] == "tool_finished" for event in events(output))
    assert events(output)[-1]["type"] == "turn_cancelled"
