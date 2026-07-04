"""Background worker entrypoint (consumer side).

Runs as its own Deployment so long/bursty work never blocks request handling and
can scale independently (on queue depth). Handlers are looked up by job type.
Run with ``python -m app.workers.worker`` (or the ``worker`` console script).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.workers.queue import QUEUE_KEY

log = get_logger("worker")

# Stable-ish id so a restarted worker can reclaim its own in-flight jobs. Set
# WORKER_ID (e.g. a StatefulSet pod name) for reliable recovery across restarts.
WORKER_ID = os.environ.get("WORKER_ID") or socket.gethostname()
PROCESSING_KEY = f"{QUEUE_KEY}:processing:{WORKER_ID}"
DEAD_KEY = f"{QUEUE_KEY}:dead"

# Map job types to async handlers. Register real handlers as you add them.
HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}


def handler(
    job_type: str,
) -> Callable[
    [Callable[[dict[str, Any]], Awaitable[None]]],
    Callable[[dict[str, Any]], Awaitable[None]],
]:
    """Decorator to register a job handler: ``@handler("send_email")``."""

    def register(
        fn: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        HANDLERS[job_type] = fn
        return fn

    return register


@handler("run_task")
async def _run_task(payload: dict[str, Any]) -> None:
    """Run an agent loop for a published task (worker execution mode)."""
    import uuid

    from app.services.runner import execute_task

    task_id = uuid.UUID(payload["task_id"])
    log.info("job.run_task", task_id=str(task_id))
    await execute_task(task_id)


async def _recover_on_startup(client: aioredis.Redis) -> None:
    """Clean up after this worker's previous incarnation crashed. (1) Fail any task
    it left stranded RUNNING (staleness-bounded, so a live sibling worker's tasks
    are untouched). (2) Move any job it was mid-processing to a dead-letter list —
    preserved for inspection, not silently lost and not auto-requeued (a job that
    crashed the process could be poison)."""
    from app.db.session import get_sessionmaker
    from app.services.runner import reconcile_interrupted_tasks

    async with get_sessionmaker()() as session:
        await reconcile_interrupted_tasks(session, stale_seconds=settings.worker_stale_task_seconds)

    dead = 0
    while await client.lmove(PROCESSING_KEY, DEAD_KEY, "LEFT", "RIGHT"):
        dead += 1
    if dead:
        log.warning("worker.dead_lettered_inflight", count=dead, key=DEAD_KEY)


async def _run() -> None:
    configure_logging()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await _recover_on_startup(client)
    log.info("worker.started", queue=QUEUE_KEY, worker_id=WORKER_ID, handlers=sorted(HANDLERS))
    try:
        while not stop.is_set():
            # Atomically move a job to our processing list so a crash mid-job doesn't
            # lose it (blpop would). Block up to 5s so SIGTERM is handled promptly.
            moved = await client.blmove(QUEUE_KEY, PROCESSING_KEY, 5, "LEFT", "LEFT")
            if moved is None:
                continue
            raw = moved if isinstance(moved, str) else moved.decode()  # decode_responses=True
            try:
                job = json.loads(raw)
                handler_fn = HANDLERS.get(job["type"])
                if handler_fn is None:
                    log.warning("job.unknown_type", type=job.get("type"))
                else:
                    await handler_fn(job.get("payload", {}))
            except Exception:  # never let one bad job kill the loop
                log.exception("job.failed", raw=raw)
            finally:
                # Done (or handled-failed): drop it from the in-flight list.
                await client.lrem(PROCESSING_KEY, 1, raw)
    finally:
        await client.aclose()
        log.info("worker.stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
