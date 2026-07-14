from __future__ import annotations

import asyncio
import builtins
import json
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        max_events_per_run: int = 200,
        max_events_total: int = 50_000,
    ) -> None:
        self.max_events_per_run = max_events_per_run
        self.max_events_total = max_events_total
        self.database_path = Path(database_path).expanduser() if database_path else None
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_events_per_run)
        )
        self._lock = asyncio.Lock()
        if self.database_path is not None:
            self._initialize_database()

    @property
    def durable(self) -> bool:
        return self.database_path is not None

    async def append(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            if self.database_path is None:
                self._events[run_id].append(event)
                return
            await asyncio.to_thread(self._append_database, run_id, event)

    async def list(self, run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            if self.database_path is None:
                return list(self._events.get(run_id, ()))
            return await asyncio.to_thread(self._list_database, run_id)

    def _connect(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise RuntimeError("Audit database is not configured")
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_database(self) -> None:
        if self.database_path is None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA journal_size_limit = 16777216")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS audit_events_run_sequence
                ON audit_events (run_id, sequence)
                """
            )

    def _append_database(self, run_id: str, event: dict[str, Any]) -> None:
        payload = json.dumps(event, separators=(",", ":"), sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_events (run_id, event_json) VALUES (?, ?)",
                (run_id, payload),
            )
            connection.execute(
                """
                DELETE FROM audit_events
                WHERE sequence IN (
                    SELECT sequence FROM audit_events
                    WHERE run_id = ?
                    ORDER BY sequence DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (run_id, self.max_events_per_run),
            )
            connection.execute(
                """
                DELETE FROM audit_events
                WHERE sequence IN (
                    SELECT sequence FROM audit_events
                    ORDER BY sequence DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.max_events_total,),
            )

    def _list_database(self, run_id: str) -> builtins.list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json FROM audit_events
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
