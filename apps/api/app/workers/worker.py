"""Redis Streams worker with leases, cross-pod recovery, retries, and a DLQ."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from typing import Any, cast

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.observability.metrics import QUEUE_JOBS
from app.workers.queue import CONSUMER_GROUP, DEAD_KEY, QUEUE_KEY, ensure_consumer_group

log = get_logger("worker")

WORKER_ID = os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}


def handler(
    job_type: str,
) -> Callable[
    [Callable[[dict[str, Any]], Awaitable[None]]],
    Callable[[dict[str, Any]], Awaitable[None]],
]:
    def register(
        fn: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        HANDLERS[job_type] = fn
        return fn

    return register


@handler("run_task")
async def _run_task(payload: dict[str, Any]) -> None:
    import uuid

    from app.services.runner import execute_task

    task_id = uuid.UUID(payload["task_id"])
    log.info("job.run_task", task_id=str(task_id))
    await execute_task(task_id)


def _decode_fields(fields: dict[Any, Any]) -> dict[str, str]:
    return {
        (key.decode() if isinstance(key, bytes) else str(key)): (
            value.decode() if isinstance(value, bytes) else str(value)
        )
        for key, value in fields.items()
    }


def _payload(fields: dict[str, str]) -> dict[str, Any]:
    value = json.loads(fields.get("payload", "{}"))
    if not isinstance(value, dict):
        raise ValueError("job payload must be an object")
    return value


async def _reset_stale_tasks() -> None:
    from app.db.session import get_sessionmaker
    from app.services.runner import reconcile_interrupted_tasks

    async with get_sessionmaker()() as session:
        await reconcile_interrupted_tasks(
            session,
            stale_seconds=settings.worker_stale_task_seconds,
            requeue=True,
        )


async def _reconcile_loop(stop: asyncio.Event) -> None:
    interval = max(30, settings.worker_stale_task_seconds // 2)
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            await _reset_stale_tasks()
        except Exception:
            log.exception("worker.reconcile_failed")


async def _pending_deliveries(client: aioredis.Redis, message_id: str) -> int:
    pending = await client.xpending_range(
        QUEUE_KEY,
        CONSUMER_GROUP,
        min=message_id,
        max=message_id,
        count=1,
    )
    if not pending:
        return 1
    value = pending[0]
    if isinstance(value, dict):
        return int(value.get("times_delivered", 1))
    return 1


async def _dead_letter(
    client: aioredis.Redis,
    message_id: str,
    fields: dict[str, str],
    error: str,
) -> None:
    dead = {**fields, "source_id": message_id, "error": error[:1000]}
    async with client.pipeline(transaction=True) as pipe:
        pipe.xadd(
            DEAD_KEY,
            cast(dict[Any, Any], dead),
            maxlen=settings.worker_queue_max_length,
            approximate=True,
        )
        pipe.xack(QUEUE_KEY, CONSUMER_GROUP, message_id)
        pipe.xdel(QUEUE_KEY, message_id)
        await pipe.execute()
    log.error("job.dead_lettered", message_id=message_id, error=error[:300])
    QUEUE_JOBS.labels(outcome="dead_lettered").inc()


async def _retry(
    client: aioredis.Redis,
    message_id: str,
    fields: dict[str, str],
    error: str,
) -> None:
    attempt = int(fields.get("attempt", "1")) + 1
    retried = {**fields, "attempt": str(attempt), "last_error": error[:1000]}
    async with client.pipeline(transaction=True) as pipe:
        pipe.xadd(
            QUEUE_KEY,
            cast(dict[Any, Any], retried),
        )
        pipe.xack(QUEUE_KEY, CONSUMER_GROUP, message_id)
        pipe.xdel(QUEUE_KEY, message_id)
        await pipe.execute()
    log.warning("job.retry", message_id=message_id, attempt=attempt, error=error[:300])
    QUEUE_JOBS.labels(outcome="retried").inc()


async def _lease_heartbeat(client: aioredis.Redis, message_id: str, stop: asyncio.Event) -> None:
    interval = max(10, settings.worker_visibility_timeout_seconds // 3)
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        await client.xclaim(
            QUEUE_KEY,
            CONSUMER_GROUP,
            WORKER_ID,
            min_idle_time=0,
            message_ids=[message_id],
            justid=True,
        )


async def _process(
    client: aioredis.Redis,
    message_id: str,
    raw_fields: dict[Any, Any],
    *,
    reclaimed: bool,
) -> None:
    fields = _decode_fields(raw_fields)
    deliveries = await _pending_deliveries(client, message_id)
    if deliveries > settings.worker_max_attempts:
        await _dead_letter(client, message_id, fields, "visibility lease expired repeatedly")
        return
    if reclaimed:
        await _reset_stale_tasks()
    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(_lease_heartbeat(client, message_id, heartbeat_stop))
    try:
        payload = _payload(fields)
        handler_fn = HANDLERS.get(fields.get("type", ""))
        if handler_fn is None:
            raise ValueError(f"unknown job type {fields.get('type')!r}")
        await handler_fn(payload)
    except Exception as exc:
        heartbeat_stop.set()
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        attempt = int(fields.get("attempt", "1"))
        if attempt >= settings.worker_max_attempts:
            await _dead_letter(client, message_id, fields, str(exc))
        else:
            await _retry(client, message_id, fields, str(exc))
        return
    heartbeat_stop.set()
    heartbeat.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat
    async with client.pipeline(transaction=True) as pipe:
        pipe.xack(QUEUE_KEY, CONSUMER_GROUP, message_id)
        pipe.xdel(QUEUE_KEY, message_id)
        await pipe.execute()
    QUEUE_JOBS.labels(outcome="completed").inc()


async def _claim_stale(client: aioredis.Redis) -> list[tuple[str, dict[Any, Any]]]:
    result = await client.xautoclaim(
        QUEUE_KEY,
        CONSUMER_GROUP,
        WORKER_ID,
        min_idle_time=settings.worker_visibility_timeout_seconds * 1000,
        start_id="0-0",
        count=10,
    )
    messages = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
    return [(str(message_id), fields) for message_id, fields in messages]


async def _run() -> None:
    configure_logging()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await ensure_consumer_group(client)
    await _reset_stale_tasks()
    reconciler = asyncio.create_task(_reconcile_loop(stop))
    scheduler: asyncio.Task[None] | None = None
    if settings.scheduler_enabled:
        from app.services.scheduler import run_scheduler

        scheduler = asyncio.create_task(run_scheduler(stop))
    log.info("worker.started", stream=QUEUE_KEY, group=CONSUMER_GROUP, worker_id=WORKER_ID)
    try:
        while not stop.is_set():
            for message_id, fields in await _claim_stale(client):
                await _process(client, message_id, fields, reclaimed=True)
            raw_messages = await client.xreadgroup(
                CONSUMER_GROUP,
                WORKER_ID,
                streams={QUEUE_KEY: ">"},
                count=1,
                block=5000,
            )
            messages = cast(list[tuple[Any, list[tuple[Any, dict[Any, Any]]]]], raw_messages or [])
            for _stream, entries in messages:
                for message_id, fields in entries:
                    await _process(client, str(message_id), fields, reclaimed=False)
    finally:
        reconciler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconciler
        if scheduler is not None:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        await client.aclose()
        log.info("worker.stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
