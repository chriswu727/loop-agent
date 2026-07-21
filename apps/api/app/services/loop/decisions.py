"""Parse one model response into a bounded loop decision."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


def _balanced_json_objects(text: str) -> list[str]:
    spans: list[str] = []
    depth, start = 0, -1
    in_string = escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start : index + 1])
                start = -1
    return spans


def extract_json(text: str) -> Any:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for span in reversed(_balanced_json_objects(cleaned)):
        try:
            parsed = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


@dataclass(frozen=True, slots=True)
class Decision:
    thought: str
    tool: str | None
    args: dict[str, Any]


class DecisionParser:
    def parse(
        self,
        content: str,
        *,
        valid_tools: set[str] | frozenset[str],
        dynamic_tools: set[str] | frozenset[str] = frozenset(),
    ) -> Decision:
        payload = extract_json(content)
        if not isinstance(payload, dict):
            return Decision("", None, {})
        thought = str(payload.get("thought", "")).strip()
        tool = payload.get("tool")
        args = payload.get("args")
        parsed_args = dict(args) if isinstance(args, dict) else {}
        if not isinstance(tool, str) or tool not in valid_tools | dynamic_tools:
            return Decision(thought, None, {})
        return Decision(thought, tool, parsed_args)
