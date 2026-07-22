"""Token allocation and bounded planner history."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.progress import HistoryEntry, compact_history


@dataclass(frozen=True, slots=True)
class ContextBudget:
    total: int
    used: int
    verification_reserve: int

    @classmethod
    def allocate(cls, total: int, used: int, reserve_cap: int) -> ContextBudget:
        reserve = min(reserve_cap, max(250, total // 5), max(0, total // 2))
        return cls(total=total, used=used, verification_reserve=reserve)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    @property
    def planning(self) -> int:
        return max(0, self.remaining - self.verification_reserve)

    def verification_after(self, planning_tokens: int) -> int:
        return max(0, self.remaining - planning_tokens)

    def delegation_after(self, planning_tokens: int) -> int:
        return max(0, self.remaining - planning_tokens)


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    recent_steps: int = 4

    def render(self, entries: list[HistoryEntry]) -> str:
        if len(entries) <= self.recent_steps:
            rendered = [entry.render() for entry in entries[:-1]]
            if entries:
                rendered.append(self._render_latest(entries[-1]))
            return "\n".join(rendered) or "(nothing yet)"
        older = entries[: -self.recent_steps]
        recent = entries[-self.recent_steps :]
        return (
            compact_history(older)
            + "\n\n[RECENT STEPS]\n"
            + "\n".join(
                self._render_latest(entry) if index == len(recent) - 1 else entry.render()
                for index, entry in enumerate(recent)
            )
        )

    @staticmethod
    def _render_latest(entry: HistoryEntry) -> str:
        limit = 2_400 if entry.tool in {"run_command", "read_file"} else None
        return entry.render(observation_limit=limit)
