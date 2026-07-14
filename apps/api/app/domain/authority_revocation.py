from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class AuthorityRevocationStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path).expanduser() if database_path else None
        self._revoked: dict[str, float] = {}
        self._lock = asyncio.Lock()
        if self.database_path is not None:
            self._initialize_database()

    @property
    def durable(self) -> bool:
        return self.database_path is not None

    async def revoke(self, run_id: str, expires_at: datetime) -> None:
        expiry = expires_at.astimezone(UTC).timestamp()
        async with self._lock:
            if self.database_path is None:
                self._purge_memory()
                self._revoked[run_id] = max(expiry, self._revoked.get(run_id, 0.0))
                return
            await asyncio.to_thread(self._revoke_database, run_id, expiry)

    async def is_revoked(self, run_id: str) -> bool:
        async with self._lock:
            if self.database_path is None:
                self._purge_memory()
                return run_id in self._revoked
            return await asyncio.to_thread(self._is_revoked_database, run_id)

    def _purge_memory(self) -> None:
        now = datetime.now(UTC).timestamp()
        self._revoked = {
            run_id: expires_at for run_id, expires_at in self._revoked.items() if expires_at > now
        }

    def _connect(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise RuntimeError("Authority revocation database is not configured")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authority_revocations (
                    run_id TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
                )
                """
            )

    def _revoke_database(self, run_id: str, expires_at: float) -> None:
        now = datetime.now(UTC).timestamp()
        with self._connect() as connection:
            connection.execute("DELETE FROM authority_revocations WHERE expires_at <= ?", (now,))
            connection.execute(
                """
                INSERT INTO authority_revocations (run_id, expires_at)
                VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    expires_at = MAX(authority_revocations.expires_at, excluded.expires_at)
                """,
                (run_id, expires_at),
            )

    def _is_revoked_database(self, run_id: str) -> bool:
        now = datetime.now(UTC).timestamp()
        with self._connect() as connection:
            connection.execute("DELETE FROM authority_revocations WHERE expires_at <= ?", (now,))
            row = connection.execute(
                "SELECT 1 FROM authority_revocations WHERE run_id = ? AND expires_at > ?",
                (run_id, now),
            ).fetchone()
        return row is not None
