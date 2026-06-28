"""Minimal job queue over Redis (producer side).

This is the *seam*, intentionally tiny. The API enqueues jobs; the worker
process drains them. When your needs grow, swap this for Celery / Arq / Dramatiq
or a managed queue while keeping the same ``enqueue`` call-site contract.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

QUEUE_KEY = "jobs:default"


async def enqueue(job_type: str, payload: dict[str, Any]) -> None:
    """Push a job onto the queue. Call this from a service."""
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    try:
        await client.rpush(QUEUE_KEY, json.dumps({"type": job_type, "payload": payload}))
    finally:
        await client.aclose()
