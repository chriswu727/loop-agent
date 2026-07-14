"""Tamper-evident step ledger.

Every step is hash-chained: a step's hash covers its own content plus the
previous step's hash, anchored at a genesis derived from the task id. Change any
recorded step — a command, an observation, a tool argument — and its hash, and
every hash after it, no longer matches. The Receipt records the chain head, so a
Receipt vouches for the entire history that produced it. This is what makes
Loop's audit trail evidence rather than a log that could be edited after the fact.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any, Protocol

GENESIS_PREFIX = "loop-ledger-genesis:"


def genesis_hash(task_id: uuid.UUID) -> str:
    return hashlib.sha256(f"{GENESIS_PREFIX}{task_id}".encode()).hexdigest()


def step_hash(
    prev_hash: str,
    *,
    number: int,
    tool: str,
    tool_args: dict[str, Any],
    observation: str,
    status: str,
    tokens: int,
    thought: str = "",
) -> str:
    fields: dict[str, Any] = {
        "n": number,
        "tool": tool,
        "args": tool_args,
        "obs": observation,
        "status": status,
        "tokens": tokens,
    }
    if thought:
        fields["thought"] = thought
    body = json.dumps(
        fields,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class _StepLike(Protocol):
    number: int
    tool: str
    tool_args: dict[str, Any]
    observation: str
    status: str
    tokens: int
    thought: str
    prev_hash: str | None
    hash: str


def verify_chain(task_id: uuid.UUID, steps: Sequence[_StepLike]) -> tuple[bool, int | None]:
    """Recompute the chain from genesis. Returns (ok, broken_at_step_number)."""
    prev = genesis_hash(task_id)
    for s in steps:  # must be ordered by number
        if s.prev_hash != prev:
            return False, s.number
        expected = step_hash(
            prev,
            number=s.number,
            tool=s.tool,
            tool_args=s.tool_args,
            observation=s.observation,
            status=s.status,
            tokens=s.tokens,
            thought=getattr(s, "thought", ""),
        )
        if s.hash != expected:
            legacy = step_hash(
                prev,
                number=s.number,
                tool=s.tool,
                tool_args=s.tool_args,
                observation=s.observation,
                status=s.status,
                tokens=s.tokens,
            )
            if s.hash != legacy:
                return False, s.number
        prev = s.hash
    return True, None
