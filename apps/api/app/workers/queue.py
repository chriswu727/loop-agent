"""Durable Redis Streams queue shared by API producers and worker consumers."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.core.config import settings
from app.observability.metrics import QUEUE_JOBS

QUEUE_KEY = "jobs:default"
DEAD_KEY = "jobs:dead"
CONSUMER_GROUP = "loop-workers"


async def ensure_consumer_group(client: aioredis.Redis) -> None:
    try:
        await client.xgroup_create(QUEUE_KEY, CONSUMER_GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue(job_type: str, payload: dict[str, Any]) -> str:
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    try:
        await ensure_consumer_group(client)
        message_id = await client.xadd(
            QUEUE_KEY,
            {
                "job_id": uuid.uuid4().hex,
                "type": job_type,
                "payload": json.dumps(payload, separators=(",", ":")),
                "attempt": "1",
                "enqueued_at": datetime.now(UTC).isoformat(),
            },
        )
        QUEUE_JOBS.labels(outcome="enqueued").inc()
        return str(message_id)
    finally:
        await client.aclose()
