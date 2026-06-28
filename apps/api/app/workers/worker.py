"""Background worker entrypoint (consumer side).

Runs as its own Deployment so long/bursty work never blocks request handling and
can scale independently (on queue depth). Handlers are looked up by job type.
Run with ``python -m app.workers.worker`` (or the ``worker`` console script).
"""

from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.workers.queue import QUEUE_KEY

log = get_logger("worker")

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


async def _run() -> None:
    configure_logging()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    log.info("worker.started", queue=QUEUE_KEY, handlers=sorted(HANDLERS))
    try:
        while not stop.is_set():
            # Block up to 5s for a job so SIGTERM is handled promptly.
            result = await client.blpop([QUEUE_KEY], timeout=5)
            if result is None:
                continue
            _, raw = result
            try:
                job = json.loads(raw)
                handler_fn = HANDLERS.get(job["type"])
                if handler_fn is None:
                    log.warning("job.unknown_type", type=job.get("type"))
                    continue
                await handler_fn(job.get("payload", {}))
            except Exception:  # never let one bad job kill the loop
                log.exception("job.failed", raw=raw)
    finally:
        await client.aclose()
        log.info("worker.stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
