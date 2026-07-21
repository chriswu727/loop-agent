"""Bound delegation so a child cannot overspend its parent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _as_int(value: object, default: int) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


@dataclass(frozen=True, slots=True)
class DelegationAllocation:
    token_budget: int
    max_steps: int


class DelegationPolicy:
    def __init__(self, *, minimum_budget: int, default_steps: int, max_steps: int) -> None:
        self.minimum_budget = minimum_budget
        self.default_steps = default_steps
        self.max_steps = max_steps

    def allocate(self, args: dict[str, Any], remaining: int) -> DelegationAllocation | None:
        if remaining < self.minimum_budget:
            return None
        token_budget = max(1, min(_as_int(args.get("token_budget"), remaining), remaining))
        max_steps = max(
            1,
            min(_as_int(args.get("max_steps"), self.default_steps), self.max_steps),
        )
        return DelegationAllocation(token_budget=token_budget, max_steps=max_steps)
