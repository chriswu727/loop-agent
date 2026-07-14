from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any


class AuditStore:
    def __init__(self, *, max_events_per_run: int = 200) -> None:
        self.max_events_per_run = max_events_per_run
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_events_per_run)
        )
        self._lock = asyncio.Lock()

    async def append(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            self._events[run_id].append(event)

    async def list(self, run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._events.get(run_id, ()))
