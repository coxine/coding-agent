from __future__ import annotations

from agent_coder.context import ContextManager


def test_context_stats_distinguish_stored_and_request_context() -> None:
    manager = ContextManager(max_chars=180)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old " * 40},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent"},
    ]

    stats = manager.stats(messages)

    assert stats["totalChars"] > stats["requestChars"]
    assert stats["messageCount"] == 4
    assert stats["requestMessageCount"] < 4
    assert stats["maxChars"] == 180
    assert stats["truncated"] is True
