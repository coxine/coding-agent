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
    assert store.load(first.id).messages[-1]["content"] == "Done."


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
