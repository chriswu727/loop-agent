from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import redis.asyncio as aioredis
from redis.exceptions import RedisError

log = logging.getLogger("loop.authority_revocation")


class AuthorityRevocationStore:
    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        redis_url: str | None = None,
        namespace: str = "loop:authority",
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser() if database_path else None
        self._redis = redis_client or (
            aioredis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            if redis_url
            else None
        )
        self._owns_redis = redis_client is None and self._redis is not None
        self._revocations_key = f"{namespace}:revocations"
        self._events_channel = f"{namespace}:revocation-events"
        self._pubsub: aioredis.client.PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._observed_revocations: set[str] = set()
        self._revoked: dict[str, float] = {}
        self._lock = asyncio.Lock()
        if self.database_path is not None and self._redis is None:
            self._initialize_database()

    @property
    def durable(self) -> bool:
        return self._redis is not None or self.database_path is not None

    @property
    def shared(self) -> bool:
        return self._redis is not None

    @property
    def backend(self) -> str:
        if self._redis is not None:
            return "redis"
        return "sqlite" if self.database_path is not None else "memory"

    async def ready(self) -> bool:
        return bool(await self._redis.ping()) if self._redis is not None else True

    async def subscribe(self, callback: Callable[[str], Awaitable[None]]) -> None:
        if self._redis is None or self._listener_task is not None:
            return
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self._events_channel)
        self._listener_task = asyncio.create_task(self._listen(callback))

    async def close(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None
        if self._pubsub is not None:
            await self._pubsub.aclose()  # type: ignore[no-untyped-call]
            self._pubsub = None
        if self._redis is not None and self._owns_redis:
            await self._redis.aclose()

    async def revoke(self, run_id: str, expires_at: datetime) -> None:
        expiry = expires_at.astimezone(UTC).timestamp()
        if self._redis is not None:
            now = datetime.now(UTC).timestamp()
            await self._redis.zremrangebyscore(self._revocations_key, "-inf", now)
            if expiry > now:
                await self._redis.zadd(self._revocations_key, {run_id: expiry}, gt=True)
                await self._redis.publish(self._events_channel, run_id)
            return
        async with self._lock:
            if self.database_path is None:
                self._purge_memory()
                self._revoked[run_id] = max(expiry, self._revoked.get(run_id, 0.0))
                return
            await asyncio.to_thread(self._revoke_database, run_id, expiry)

    async def is_revoked(self, run_id: str) -> bool:
        if self._redis is not None:
            now = datetime.now(UTC).timestamp()
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.zremrangebyscore(self._revocations_key, "-inf", now)
                pipeline.zscore(self._revocations_key, run_id)
                _, expiry = await pipeline.execute()
            return expiry is not None and float(expiry) > now
        async with self._lock:
            if self.database_path is None:
                self._purge_memory()
                return run_id in self._revoked
            return await asyncio.to_thread(self._is_revoked_database, run_id)

    async def _listen(self, callback: Callable[[str], Awaitable[None]]) -> None:
        if self._pubsub is None or self._redis is None:
            return
        while True:
            try:
                message = await self._pubsub.get_message(timeout=1.0)
                if message is not None:
                    run_id = message.get("data")
                    if isinstance(run_id, str):
                        await self._notify(callback, run_id)
                now = datetime.now(UTC).timestamp()
                active = set(
                    cast(
                        list[str],
                        await self._redis.zrangebyscore(self._revocations_key, now, "+inf"),
                    )
                )
                self._observed_revocations.intersection_update(active)
                for run_id in active - self._observed_revocations:
                    await self._notify(callback, run_id)
            except asyncio.CancelledError:
                raise
            except RedisError:
                log.exception("Authority revocation subscriber lost Redis")
                await asyncio.sleep(1)

    async def _notify(
        self,
        callback: Callable[[str], Awaitable[None]],
        run_id: str,
    ) -> None:
        if run_id in self._observed_revocations:
            return
        try:
            await callback(run_id)
        except Exception:
            log.exception("Authority revocation subscriber failed")
        else:
            self._observed_revocations.add(run_id)

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
