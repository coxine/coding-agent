from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

# Context budget fractions relative to max_chars. The gap between the trigger
# and the hard limit leaves execution headroom for a single large tool result.
TRIGGER_RATIO = 0.70
HARD_RATIO = 0.85
RECENT_GROUPS = 6

# Tool calls that are cheap to re-run and whose output is disposable status.
_DISPOSABLE_TOOLS = {"list_directory", "git_status", "git_diff"}

_PATH_KEYS = ("path", "source", "destination")

SummarizeCallback = Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any] | None]]


def _size(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False, default=str))


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


@dataclass(slots=True)
class CompactState:
    goal: str = ""
    constraints: list[str] = field(default_factory=list)
    confirmed_facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    rejected_approaches: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    relevant_symbols: list[str] = field(default_factory=list)
    validation_passed: list[str] = field(default_factory=list)
    validation_failed: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "confirmedFacts": list(self.confirmed_facts),
            "decisions": list(self.decisions),
            "rejectedApproaches": list(self.rejected_approaches),
            "modifiedFiles": list(self.modified_files),
            "relevantFiles": list(self.relevant_files),
            "relevantSymbols": list(self.relevant_symbols),
            "validationPassed": list(self.validation_passed),
            "validationFailed": list(self.validation_failed),
            "openQuestions": list(self.open_questions),
            "nextSteps": list(self.next_steps),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CompactState":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            goal=_as_str(raw.get("goal")),
            constraints=_as_str_list(raw.get("constraints")),
            confirmed_facts=_as_str_list(raw.get("confirmedFacts")),
            decisions=_as_str_list(raw.get("decisions")),
            rejected_approaches=_as_str_list(raw.get("rejectedApproaches")),
            modified_files=_as_str_list(raw.get("modifiedFiles")),
            relevant_files=_as_str_list(raw.get("relevantFiles")),
            relevant_symbols=_as_str_list(raw.get("relevantSymbols")),
            validation_passed=_as_str_list(raw.get("validationPassed")),
            validation_failed=_as_str_list(raw.get("validationFailed")),
            open_questions=_as_str_list(raw.get("openQuestions")),
            next_steps=_as_str_list(raw.get("nextSteps")),
        )

    def render_block(self) -> str:
        sections: list[tuple[str, list[str]]] = []
        if self.goal:
            sections.append(("Task", [self.goal]))
        if self.constraints:
            sections.append(("Constraints", self.constraints))
        if self.confirmed_facts:
            sections.append(("Confirmed findings", self.confirmed_facts))
        if self.decisions:
            sections.append(("Decisions", self.decisions))
        if self.rejected_approaches:
            sections.append(("Rejected approaches", self.rejected_approaches))
        if self.modified_files:
            sections.append(("Modified files", self.modified_files))
        if self.relevant_files:
            sections.append(("Relevant files", self.relevant_files))
        if self.relevant_symbols:
            sections.append(("Relevant symbols", self.relevant_symbols))
        if self.validation_passed:
            sections.append(("Validation passed", self.validation_passed))
        if self.validation_failed:
            sections.append(("Validation failed", self.validation_failed))
        if self.open_questions:
            sections.append(("Open questions", self.open_questions))
        if self.next_steps:
            sections.append(("Next steps", self.next_steps))

        lines: list[str] = []
        for title, items in sections:
            lines.append(f"# {title}")
            lines.extend(f"- {item}" for item in items)
            lines.append("")
        return "\n".join(lines).rstrip()


@dataclass(slots=True)
class Group:
    kind: str
    messages: list[dict[str, Any]]


def split_groups(messages: list[dict[str, Any]]) -> list[Group]:
    groups: list[Group] = []
    current: Group | None = None
    for message in messages:
        role = message.get("role")
        if role == "system":
            groups.append(Group("system", [message]))
            current = None
        elif role == "user":
            groups.append(Group("user", [message]))
            current = None
        elif role == "assistant":
            if message.get("tool_calls"):
                current = Group("tool_call", [message])
                groups.append(current)
            else:
                groups.append(Group("assistant", [message]))
                current = None
        elif role == "tool":
            if current is not None and current.kind == "tool_call":
                current.messages.append(message)
            else:
                groups.append(Group("tool", [message]))
                current = None
        elif current is not None:
            current.messages.append(message)
        else:
            groups.append(Group("other", [message]))
    return groups


def _flatten_groups(groups: list[Group]) -> list[dict[str, Any]]:
    return [deepcopy(message) for group in groups for message in group.messages]


def _group_tool_names(group: Group) -> list[str]:
    names: list[str] = []
    for message in group.messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            if isinstance(function.get("name"), str):
                names.append(function["name"])
    return names


def _group_result_summaries(group: Group) -> list[str]:
    summaries: list[str] = []
    for message in group.messages:
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(message.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(result, dict) and isinstance(result.get("summary"), str):
            summaries.append(result["summary"])
    return summaries


def _group_fingerprint(group: Group) -> str | None:
    if group.kind != "tool_call":
        return None
    names = _group_tool_names(group)
    summaries = _group_result_summaries(group)
    return json.dumps({"names": names, "summaries": summaries}, ensure_ascii=False)


def drop_observations(groups: list[Group]) -> list[Group]:
    return [
        group
        for group in groups
        if not (
            group.kind == "tool_call"
            and _group_tool_names(group)
            and all(name in _DISPOSABLE_TOOLS for name in _group_tool_names(group))
        )
    ]


def dedupe_groups(groups: list[Group]) -> list[Group]:
    result: list[Group] = []
    seen: set[str] = set()
    for group in groups:
        fingerprint = _group_fingerprint(group)
        if fingerprint is not None:
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
        result.append(group)
    return result


def _collapse_tool_content(content: str) -> str:
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content
    if not isinstance(result, dict):
        return content
    compact: dict[str, Any] = {
        "ok": result.get("ok"),
        "summary": result.get("summary"),
    }
    if result.get("error"):
        compact["error"] = result["error"]
    data = result.get("data")
    if isinstance(data, dict):
        if data.get("content") is not None:
            compact["note"] = "full content elided; re-read with read_file"
        elif data.get("diff") is not None:
            compact["note"] = "diff elided; re-read with git_diff"
        elif data.get("stdout") is not None:
            compact["note"] = "stdout elided"
    return json.dumps(compact, ensure_ascii=False)


def collapse_groups(groups: list[Group]) -> list[Group]:
    result: list[Group] = []
    for group in groups:
        if group.kind != "tool_call":
            result.append(group)
            continue
        collapsed = [
            (
                {**message, "content": _collapse_tool_content(message["content"])}
                if message.get("role") == "tool"
                else message
            )
            for message in group.messages
        ]
        result.append(Group("tool_call", collapsed))
    return result


_SUMMARY_FIELDS = {
    "goal": "str",
    "constraints": "list",
    "confirmedFacts": "list",
    "decisions": "list",
    "rejectedApproaches": "list",
    "modifiedFiles": "list",
    "relevantFiles": "list",
    "relevantSymbols": "list",
    "validationPassed": "list",
    "validationFailed": "list",
    "openQuestions": "list",
    "nextSteps": "list",
}


def validate_summary(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    validated: dict[str, Any] = {}
    for key, kind in _SUMMARY_FIELDS.items():
        if key not in raw:
            continue
        value = raw[key]
        if kind == "str":
            text = _as_str(value)
            if text:
                validated[key] = text
        else:
            items = _as_str_list(value)
            if items:
                validated[key] = items
    return validated or None


def merge_state(state: CompactState, summary: dict[str, Any]) -> CompactState:
    merged = deepcopy(state)
    mapping = {
        "goal": "goal",
        "constraints": "constraints",
        "confirmedFacts": "confirmed_facts",
        "decisions": "decisions",
        "rejectedApproaches": "rejected_approaches",
        "modifiedFiles": "modified_files",
        "relevantFiles": "relevant_files",
        "relevantSymbols": "relevant_symbols",
        "validationPassed": "validation_passed",
        "validationFailed": "validation_failed",
        "openQuestions": "open_questions",
        "nextSteps": "next_steps",
    }
    for key, field_name in mapping.items():
        value = summary.get(key)
        if isinstance(value, str):
            if not getattr(merged, field_name):
                setattr(merged, field_name, value)
        elif isinstance(value, list):
            existing = getattr(merged, field_name)
            seen = set(existing)
            for item in value:
                if item not in seen:
                    existing.append(item)
                    seen.add(item)
    return merged


_SUMMARY_INSTRUCTION = (
    "You are compacting a coding agent's conversation history into durable task state. "
    "Extract conclusions, never verbatim transcripts. Return ONLY a JSON object with "
    "these fields (all optional; goal is a string, every other field is an array of strings): "
    "goal, constraints, confirmedFacts, decisions, rejectedApproaches, modifiedFiles, "
    "relevantFiles, relevantSymbols, validationPassed, validationFailed, openQuestions, nextSteps. "
    "Rules: preserve negative constraints (e.g. 'do not modify the API'), user corrections, "
    "unresolved bugs, API contracts, and failed tests. Include a rejectedApproaches entry only "
    "when the agent might plausibly retry it. Omit logs, search outputs, old file bodies, "
    "greetings, and redundant observations. Do not reproduce file contents; prefer file paths "
    "and symbol references."
)


class MemoryManager:
    def __init__(
        self,
        max_chars: int,
        *,
        summarize: SummarizeCallback | None = None,
        state: CompactState | None = None,
        recent_groups: int = RECENT_GROUPS,
    ) -> None:
        self.max_chars = max_chars
        self._summarize = summarize
        self.state = state if state is not None else CompactState()
        self.recent_groups = recent_groups
        self.compact_count = 0
        self._trigger = max_chars * TRIGGER_RATIO
        self._hard = max_chars * HARD_RATIO

    async def build_request(
        self, messages: list[dict[str, Any]], changed_files: set[str] | None = None
    ) -> list[dict[str, Any]]:
        if changed_files:
            self._merge(self.state.modified_files, sorted(changed_files))

        total = sum(_size(message) for message in messages)
        if total <= self._trigger:
            return messages

        self.compact_count += 1
        request, _summarized = await self._run_compaction(messages)
        return request

    async def compact_request(
        self, messages: list[dict[str, Any]], changed_files: set[str] | None = None
    ) -> list[dict[str, Any]]:
        if changed_files:
            self._merge(self.state.modified_files, sorted(changed_files))

        self.compact_count += 1
        request, _summarized = await self._run_compaction(messages)
        return request

    async def force_compact(
        self, messages: list[dict[str, Any]], changed_files: set[str] | None = None
    ) -> dict[str, Any]:
        if changed_files:
            self._merge(self.state.modified_files, sorted(changed_files))

        self.compact_count += 1
        request, summarized = await self._run_compaction(messages)
        return {
            "summarized": summarized,
            "messageCountBefore": len(messages),
            "messageCountAfter": len(request),
            "stateCounts": self._state_counts(),
        }

    async def _run_compaction(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], bool]:
        groups = split_groups(messages)
        system, goal, middle, recent = self._partition(groups)
        self._extract_durable([*middle, *recent])
        middle = drop_observations(middle)
        middle = dedupe_groups(middle)
        middle = collapse_groups(middle)

        summarized = False
        if self._summarize is not None and middle:
            try:
                summary = await self._summarize(self._summary_prompt(middle))
                validated = validate_summary(summary)
                if validated is not None:
                    self.state = merge_state(self.state, validated)
                    middle = []
                    summarized = True
            except Exception:
                pass

        ordered = self._ordered_groups(system, goal, middle, recent)
        ordered = self._hard_clamp(ordered, system, goal)
        return _flatten_groups(self._render(ordered)), summarized

    def _state_counts(self) -> dict[str, Any]:
        return {
            "goal": bool(self.state.goal),
            "constraints": len(self.state.constraints),
            "confirmedFacts": len(self.state.confirmed_facts),
            "decisions": len(self.state.decisions),
            "rejectedApproaches": len(self.state.rejected_approaches),
            "modifiedFiles": len(self.state.modified_files),
            "relevantFiles": len(self.state.relevant_files),
        }

    def stats(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        total = sum(_size(message) for message in messages)
        return {
            "totalChars": total,
            "requestChars": total,
            "maxChars": self.max_chars,
            "messageCount": len(messages),
            "requestMessageCount": len(messages),
            "truncated": total > self._trigger,
            "compacted": self.compact_count > 0,
            "stateCounts": self._state_counts(),
        }

    def _partition(
        self, groups: list[Group]
    ) -> tuple[Group, Group | None, list[Group], list[Group]]:
        system = groups[0]
        rest = groups[1:]
        goal = next((group for group in rest if group.kind == "user"), None)
        recent = rest[-self.recent_groups :] if self.recent_groups > 0 else []
        excluded = {id(goal)} if goal is not None else set()
        excluded.update(id(group) for group in recent)
        middle = [group for group in rest if id(group) not in excluded]
        return system, goal, middle, recent

    def _extract_durable(self, groups: list[Group]) -> None:
        for group in groups:
            for message in group.messages:
                if message.get("role") != "assistant":
                    continue
                for call in message.get("tool_calls", []):
                    function = call.get("function", {})
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    if not isinstance(arguments, dict):
                        continue
                    for key in _PATH_KEYS:
                        value = arguments.get(key)
                        if isinstance(value, str) and value.strip():
                            self._merge(self.state.relevant_files, [value])
            for message in group.messages:
                if message.get("role") != "tool":
                    continue
                try:
                    result = json.loads(message.get("content") or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(result, dict):
                    continue
                summary = result.get("summary")
                if not isinstance(summary, str) or not summary:
                    continue
                target = (
                    self.state.validation_passed
                    if result.get("ok")
                    else self.state.validation_failed
                )
                self._merge(target, [summary])

    def _summary_prompt(self, groups: list[Group]) -> list[dict[str, Any]]:
        rendered: list[str] = []
        budget = 30_000
        for message in _flatten_groups(groups):
            role = message.get("role")
            content = message.get("content")
            if content is None:
                content = json.dumps(message.get("tool_calls"), ensure_ascii=False)
            text = f"{role}: {content}"
            if len(text) > 4_000:
                text = text[:4_000] + "..."
            rendered.append(text)
            budget -= len(text)
            if budget <= 0:
                rendered.append("... truncated ...")
                break
        return [
            {"role": "system", "content": _SUMMARY_INSTRUCTION},
            {
                "role": "user",
                "content": "Compact this conversation:\n\n" + "\n\n".join(rendered),
            },
        ]

    @staticmethod
    def _ordered_groups(
        system: Group, goal: Group | None, middle: list[Group], recent: list[Group]
    ) -> list[Group]:
        ordered: list[Group] = [system]
        if goal is not None and not any(group is goal for group in recent):
            ordered.append(goal)
        ordered.extend(middle)
        ordered.extend(recent)
        return ordered

    def _hard_clamp(
        self, ordered: list[Group], system: Group, goal: Group | None
    ) -> list[Group]:
        total = sum(_size(message) for group in ordered for message in group.messages)
        if total <= self._hard:
            return ordered
        removable = [group for group in ordered if group is not system and group is not goal]
        index = 0
        while total > self._hard and index < len(removable):
            total -= sum(_size(message) for message in removable[index].messages)
            index += 1
        dropped = {id(group) for group in removable[:index]}
        return [
            group
            for group in ordered
            if group is system or group is goal or id(group) not in dropped
        ]

    def _render(self, ordered: list[Group]) -> list[Group]:
        if not ordered or ordered[0].kind != "system":
            return ordered
        block = self.state.render_block()
        system = ordered[0]
        content = system.messages[0].get("content") or ""
        if block:
            content = f"{content}\n\n{block}"
        rendered = [Group("system", [{"role": "system", "content": content}])]
        rendered.extend(deepcopy(group) for group in ordered[1:])
        return rendered

    @staticmethod
    def _merge(target: list[str], items: list[str]) -> None:
        seen = set(target)
        for item in items:
            if item and item not in seen:
                target.append(item)
                seen.add(item)
