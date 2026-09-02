from __future__ import annotations

import json

import pytest

from agent_coder.memory import (
    CompactState,
    Group,
    MemoryManager,
    collapse_groups,
    dedupe_groups,
    drop_observations,
    merge_state,
    split_groups,
    validate_summary,
)


def _tool_group(name: str, args: dict, summary: str, *, data: dict | None = None, ok: bool = True) -> list[dict]:
    call_id = f"call_{name}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(
                {"ok": ok, "summary": summary, "data": data, "error": None}
            ),
        },
    ]


def _trajectory(group_count: int) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": "system prompt"}]
    messages.append({"role": "user", "content": "Fix the parser"})
    for index in range(group_count):
        messages.extend(
            _tool_group(
                "read_file",
                {"path": f"src/f{index}.py"},
                f"Read src/f{index}.py",
                data={"content": f"file{index}:" + "z" * 500},
            )
        )
    messages.append({"role": "assistant", "content": "Done"})
    return messages


def test_split_groups_keeps_tool_calls_atomic() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"ok": True})},
        {"role": "assistant", "content": "done"},
    ]
    groups = split_groups(messages)

    assert [group.kind for group in groups] == ["system", "user", "tool_call", "assistant"]
    assert len(groups[2].messages) == 2


def test_drop_observations_removes_disposable_status() -> None:
    groups = [
        Group("system", [{"role": "system", "content": "sys"}]),
        Group("user", [{"role": "user", "content": "hi"}]),
        Group("tool_call", _tool_group("git_status", {}, "Working tree is clean")),
        Group("tool_call", _tool_group("read_file", {"path": "a.py"}, "Read a.py")),
    ]
    kept = drop_observations(groups)
    names = [g.kind for g in kept]
    assert names == ["system", "user", "tool_call"]
    assert kept[-1].messages[0]["tool_calls"][0]["function"]["name"] == "read_file"


def test_dedupe_removes_duplicate_tool_groups() -> None:
    groups = [
        Group("tool_call", _tool_group("read_file", {"path": "a.py"}, "Read a.py")),
        Group("tool_call", _tool_group("read_file", {"path": "a.py"}, "Read a.py")),
        Group("tool_call", _tool_group("read_file", {"path": "b.py"}, "Read b.py")),
    ]
    kept = dedupe_groups(groups)
    assert len(kept) == 2


def test_collapse_elides_tool_data_and_keeps_summary() -> None:
    groups = [Group("tool_call", _tool_group("read_file", {"path": "a.py"}, "Read a.py", data={"content": "x" * 1000}))]
    collapsed = collapse_groups(groups)
    tool_content = collapsed[0].messages[1]["content"]
    parsed = json.loads(tool_content)
    assert parsed["summary"] == "Read a.py"
    assert "content" not in parsed
    assert "x" * 100 not in tool_content


def test_validate_summary_accepts_valid_and_rejects_invalid() -> None:
    assert validate_summary({"goal": "Fix parser", "constraints": ["no deps"]}) == {
        "goal": "Fix parser",
        "constraints": ["no deps"],
    }
    assert validate_summary({"constraints": ["  "]}) is None
    assert validate_summary("not a dict") is None
    assert validate_summary({}) is None


def test_compact_state_round_trip_and_render() -> None:
    state = CompactState(
        goal="Fix parser",
        constraints=["no new deps"],
        modified_files=["src/config.py"],
    )
    restored = CompactState.from_dict(state.to_dict())
    assert restored.goal == "Fix parser"
    assert restored.constraints == ["no new deps"]

    block = state.render_block()
    assert "# Task" in block
    assert "Fix parser" in block
    assert "# Constraints" in block
    assert "# Modified files" in block


def test_merge_state_accumulates_lists() -> None:
    state = CompactState(constraints=["a"])
    merged = merge_state(state, {"constraints": ["b"], "goal": "g", "decisions": ["d"]})
    assert merged.constraints == ["a", "b"]
    assert merged.goal == "g"
    assert merged.decisions == ["d"]


@pytest.mark.asyncio
async def test_build_request_under_trigger_returns_unchanged() -> None:
    manager = MemoryManager(max_chars=100_000)
    messages = _trajectory(1)
    request = await manager.build_request(messages)
    assert request == messages
    assert manager.compact_count == 0


@pytest.mark.asyncio
async def test_build_request_compacts_and_injects_summary() -> None:
    calls: list[list[dict]] = []

    async def summarize(messages: list[dict]) -> dict:
        calls.append(messages)
        return {
            "goal": "Fix the parser",
            "constraints": ["no new dependencies"],
            "confirmedFacts": ["root cause is parseConfig"],
            "modifiedFiles": ["src/config.py"],
        }

    manager = MemoryManager(max_chars=7000, summarize=summarize)
    request = await manager.build_request(_trajectory(8))

    assert len(calls) == 1
    system = request[0]
    assert system["role"] == "system"
    assert "# Task" in system["content"]
    assert "Fix the parser" in system["content"]
    assert "# Constraints" in system["content"]
    # goal user message preserved as the first conversational message
    assert request[1]["role"] == "user"
    # middle trajectory (old read_file) was summarized away
    assert not any("file0:" in json.dumps(message) for message in request)
    # recent group still present with raw content
    assert any("file7:" in json.dumps(message) for message in request)


@pytest.mark.asyncio
async def test_build_request_without_summarizer_keeps_collapsed() -> None:
    manager = MemoryManager(max_chars=7000, summarize=None)
    request = await manager.build_request(_trajectory(8))

    # old middle group collapsed: data elided but summary retained
    assert any("Read src/f0.py" in json.dumps(message) for message in request)
    assert not any("file0:" in json.dumps(message) for message in request)
    # recent group kept raw
    assert any("file7:" in json.dumps(message) for message in request)
    # invariants: system first, no orphan leading tool message
    assert request[0]["role"] == "system"
    assert request[1]["role"] == "user"


@pytest.mark.asyncio
async def test_force_compact_runs_unconditionally() -> None:
    manager = MemoryManager(max_chars=1_000_000, summarize=None)
    messages = _trajectory(8)

    info = await manager.force_compact(messages)

    assert manager.compact_count == 1
    assert info["summarized"] is False
    assert info["messageCountBefore"] == len(messages)
    assert info["messageCountAfter"] == len(messages)
    assert "stateCounts" in info


@pytest.mark.asyncio
async def test_force_compact_with_summarizer_reports_counts() -> None:
    calls: list[list[dict]] = []

    async def summarize(messages: list[dict]) -> dict:
        calls.append(messages)
        return {"goal": "Fix the parser"}

    manager = MemoryManager(max_chars=1_000_000, summarize=summarize)
    messages = _trajectory(8)

    info = await manager.force_compact(messages)

    assert info["summarized"] is True
    assert info["messageCountBefore"] == len(messages)
    assert info["messageCountAfter"] < len(messages)
    assert info["stateCounts"]["goal"] is True
    assert len(calls) == 1
