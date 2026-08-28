from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


class ContextManager:
    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars

    @staticmethod
    def _size(message: dict[str, Any]) -> int:
        return len(json.dumps(message, ensure_ascii=False, default=str))

    def build_request(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request = deepcopy(messages)
        total = sum(self._size(message) for message in request)
        if total <= self.max_chars:
            return request

        # Preserve system prompt and recent history. Remove complete old turns/groups;
        # never leave a tool message at the start of retained conversational history.
        system = request[:1]
        tail = request[1:]
        while tail and total > self.max_chars:
            removed = tail.pop(0)
            total -= self._size(removed)
            while tail and tail[0].get("role") == "tool":
                total -= self._size(tail.pop(0))

        return system + tail

