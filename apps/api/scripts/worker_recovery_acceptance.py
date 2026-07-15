#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import redis.asyncio as aioredis

from app.workers import worker
from app.workers.queue import CONSUMER_GROUP, DEAD_KEY, QUEUE_KEY, ensure_consumer_group

JOB_TYPE = "acceptance-worker-recovery"
PAYLOAD = {"proof": "survived-worker-crash"}


async def _claim(redis_url: str) -> None:
    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.delete(QUEUE_KEY, DEAD_KEY)
        await ensure_consumer_group(client)
        message_id = await client.xadd(
            QUEUE_KEY,
            {
                "type": JOB_TYPE,
                "payload": json.dumps(PAYLOAD, separators=(",", ":")),
                "attempt": "1",
            },
        )
        messages = await client.xreadgroup(
            CONSUMER_GROUP,
            "acceptance-crashed-worker",
            streams={QUEUE_KEY: ">"},
            count=1,
        )
        if not messages or str(messages[0][1][0][0]) != str(message_id):
            raise RuntimeError("Crash worker did not claim the acceptance job")
        pending = await client.xpending(QUEUE_KEY, CONSUMER_GROUP)
        if int(pending["pending"]) != 1:
            raise RuntimeError(f"Expected one unacknowledged job, got {pending!r}")
        print(
            json.dumps(
                {
                    "status": "claimed-before-crash",
                    "message_id": str(message_id),
                    "pending": 1,
                },
                sort_keys=True,
            )
        )
    finally:
        await client.aclose()


async def _recover(redis_url: str) -> None:
    client = aioredis.from_url(redis_url, decode_responses=True)
    handled: list[dict[str, Any]] = []

    async def recovered_handler(payload: dict[str, Any]) -> None:
        handled.append(payload)

    async def reconciled_stale_tasks() -> None:
        return None

    try:
        await ensure_consumer_group(client)
        claimed = await client.xautoclaim(
            QUEUE_KEY,
            CONSUMER_GROUP,
            "acceptance-recovery-worker",
            min_idle_time=0,
            start_id="0-0",
            count=10,
        )
        messages = claimed[1] if len(claimed) > 1 else []
        if len(messages) != 1:
            raise RuntimeError(f"Recovery worker expected one abandoned job, got {messages!r}")

        worker.HANDLERS[JOB_TYPE] = recovered_handler
        worker._reset_stale_tasks = reconciled_stale_tasks
        message_id, fields = messages[0]
        await worker._process(client, str(message_id), fields, reclaimed=True)

        pending = await client.xpending(QUEUE_KEY, CONSUMER_GROUP)
        remaining = await client.xlen(QUEUE_KEY)
        dead = await client.xlen(DEAD_KEY)
        if handled != [PAYLOAD] or int(pending["pending"]) != 0 or remaining != 0 or dead != 0:
            raise RuntimeError(
                "Abandoned job was not recovered exactly once: "
                f"handled={handled!r}, pending={pending!r}, remaining={remaining}, dead={dead}"
            )
        print(
            json.dumps(
                {
                    "status": "recovered-after-crash",
                    "message_id": str(message_id),
                    "handled_exactly_once": True,
                    "pending": 0,
                },
                sort_keys=True,
            )
        )
    finally:
        await client.delete(QUEUE_KEY, DEAD_KEY)
        await client.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fault-inject a worker crash and recovery")
    parser.add_argument("command", choices=("claim", "recover"))
    parser.add_argument("--redis-url", required=True)
    parser.add_argument(
        "--allow-destructive-test",
        action="store_true",
        help="required acknowledgement that the disposable worker queue will be cleared",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_destructive_test:
        raise SystemExit("Refusing to clear the worker queue without --allow-destructive-test")
    asyncio.run(_claim(args.redis_url) if args.command == "claim" else _recover(args.redis_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
