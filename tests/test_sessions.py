from __future__ import annotations

import json
import stat

import pytest

from agent_coder.sessions import SessionStore


def test_session_round_trip_and_latest_order(tmp_path) -> None:
    store = SessionStore(tmp_path)
    first = store.create()
    store.update_messages(
        first,
        [
            {"role": "user", "content": "Fix the parser"},
            {"role": "assistant", "content": "Done."},
        ],
    )
    second = store.create()

    summaries = store.list()
    assert summaries[0]["id"] == second.id
    assert summaries[1]["title"] == "Fix the parser"
    assert summaries[1]["titleSource"] == "auto"
    assert store.load(first.id).messages[-1]["content"] == "Done."


def test_custom_session_name_survives_later_messages(tmp_path) -> None:
    store = SessionStore(tmp_path)
    conversation = store.create()
    store.update_messages(conversation, [{"role": "user", "content": "First prompt"}])
    assert conversation.title == "First prompt"
    assert conversation.title_source == "auto"

    store.rename(conversation, "  Parser   work  ")
    store.update_messages(
        conversation,
        [
            {"role": "user", "content": "First prompt"},
            {"role": "assistant", "content": "Done"},
            {"role": "user", "content": "Another prompt"},
        ],
    )

    restored = store.load(conversation.id)
    assert restored.title == "Parser work"
    assert restored.title_source == "custom"


def test_legacy_session_without_title_source_loads_as_auto(tmp_path) -> None:
    store = SessionStore(tmp_path)
    conversation = store.create()
    path = store.directory / f"{conversation.id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("titleSource")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load(conversation.id).title_source == "auto"


def test_session_records_exact_usage_per_model_request(tmp_path) -> None:
    store = SessionStore(tmp_path)
    conversation = store.create()
    store.record_usage(
        conversation,
        turn_id="turn_1",
        step=1,
        usage={"promptTokens": 100, "completionTokens": 20, "totalTokens": 120},
    )
    store.record_usage(conversation, turn_id="turn_1", step=2, usage=None)
    store.record_usage(
        conversation,
        turn_id="turn_2",
        step=1,
        usage={
            "promptTokens": 150,
            "completionTokens": 30,
            "totalTokens": 180,
            "cachedTokens": 50,
            "reasoningTokens": 10,
        },
    )

    restored = store.load(conversation.id)
    summary = store.usage_summary(restored)
    assert summary["requestCount"] == 3
    assert summary["measuredRequests"] == 2
    assert summary["unavailableRequests"] == 1
    assert summary["latest"]["promptTokens"] == 150
    assert summary["latestMeasured"]["promptTokens"] == 150
    assert summary["totals"] == {
        "promptTokens": 250,
        "completionTokens": 50,
        "totalTokens": 300,
        "cachedTokens": 50,
        "reasoningTokens": 10,
    }


def test_session_files_are_private_and_atomic_temps_are_cleaned(tmp_path) -> None:
    store = SessionStore(tmp_path)
    conversation = store.create()
    store.update_messages(conversation, [{"role": "user", "content": "hello"}])

    path = store.directory / f"{conversation.id}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert list(store.directory.glob("*.tmp")) == []


def test_corrupt_sessions_are_ignored_and_invalid_ids_are_rejected(tmp_path) -> None:
    store = SessionStore(tmp_path)
    (store.directory / "conv_00000000000000000000000000000000.json").write_text(
        "not json", encoding="utf-8"
    )
    assert store.list() == []
    with pytest.raises(ValueError, match="invalid conversation id"):
        store.load("../../secret")


def test_transcript_omits_tools_and_empty_assistant_messages(tmp_path) -> None:
    store = SessionStore(tmp_path)
    messages = [
        {"role": "user", "content": "Inspect it"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "content": json.dumps({"ok": True}), "tool_call_id": "call_1"},
        {"role": "assistant", "content": "It is valid."},
    ]
    assert store.transcript(messages) == [
        {"role": "user", "content": "Inspect it"},
        {"role": "assistant", "content": "It is valid."},
    ]


def test_compact_state_round_trips_and_defaults(tmp_path) -> None:
    store = SessionStore(tmp_path)
    conversation = store.create()
    store.update_messages(
        conversation,
        [{"role": "user", "content": "Fix the parser"}],
        compact_state={"goal": "Fix the parser", "constraints": ["no deps"]},
    )

    restored = store.load(conversation.id)
    assert restored.compact_state == {"goal": "Fix the parser", "constraints": ["no deps"]}

    legacy = store.create()
    assert legacy.compact_state == {}
