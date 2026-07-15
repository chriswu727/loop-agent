from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.workers import worker


class FakePipeline:
    def __init__(self, actions: list[tuple[str, tuple[Any, ...], dict[str, Any]]]) -> None:
        self.actions = actions

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def xadd(self, *args: Any, **kwargs: Any) -> None:
        self.actions.append(("xadd", args, kwargs))

    def xack(self, *args: Any, **kwargs: Any) -> None:
        self.actions.append(("xack", args, kwargs))

    def xdel(self, *args: Any, **kwargs: Any) -> None:
        self.actions.append(("xdel", args, kwargs))

    async def execute(self) -> list[object]:
        return []


class FakeRedis:
    def __init__(self) -> None:
        self.actions: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def xpending_range(self, *_args: Any, **_kwargs: Any) -> list[dict[str, int]]:
        return [{"times_delivered": 1}]

    def pipeline(self, **_kwargs: Any) -> FakePipeline:
        return FakePipeline(self.actions)


async def failing_handler(_payload: dict[str, Any]) -> None:
    raise RuntimeError("transient failure")


async def successful_handler(payload: dict[str, Any]) -> None:
    assert payload == {"task_id": "task-1"}


async def test_failed_job_is_requeued_with_incremented_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_max_attempts", 3)
    monkeypatch.setitem(worker.HANDLERS, "test-failure", failing_handler)
    redis = FakeRedis()

    await worker._process(
        redis,  # type: ignore[arg-type]
        "1-0",
        {"type": "test-failure", "payload": "{}", "attempt": "1"},
        reclaimed=False,
    )

    xadd = next(action for action in redis.actions if action[0] == "xadd")
    assert xadd[1][0] == worker.QUEUE_KEY
    assert xadd[1][1]["attempt"] == "2"
    assert any(action[0] == "xack" for action in redis.actions)
    assert any(action[0] == "xdel" for action in redis.actions)


async def test_exhausted_job_moves_to_dead_letter_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_max_attempts", 3)
    monkeypatch.setitem(worker.HANDLERS, "test-failure", failing_handler)
    redis = FakeRedis()

    await worker._process(
        redis,  # type: ignore[arg-type]
        "2-0",
        {"type": "test-failure", "payload": "{}", "attempt": "3"},
        reclaimed=False,
    )

    xadd = next(action for action in redis.actions if action[0] == "xadd")
    assert xadd[1][0] == worker.DEAD_KEY
    assert xadd[1][1]["source_id"] == "2-0"
    assert "transient failure" in xadd[1][1]["error"]


async def test_successful_job_is_acknowledged_and_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(worker.HANDLERS, "test-success", successful_handler)
    redis = FakeRedis()

    await worker._process(
        redis,  # type: ignore[arg-type]
        "3-0",
        {"type": "test-success", "payload": '{"task_id":"task-1"}', "attempt": "1"},
        reclaimed=False,
    )

    assert [action[0] for action in redis.actions] == ["xack", "xdel"]


async def test_reclaimed_job_reconciles_stale_tasks_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciled = False

    async def reconcile() -> None:
        nonlocal reconciled
        reconciled = True

    monkeypatch.setattr(worker, "_reset_stale_tasks", reconcile)
    monkeypatch.setitem(worker.HANDLERS, "test-success", successful_handler)
    redis = FakeRedis()

    await worker._process(
        redis,  # type: ignore[arg-type]
        "4-0",
        {"type": "test-success", "payload": '{"task_id":"task-1"}', "attempt": "1"},
        reclaimed=True,
    )

    assert reconciled
    assert [action[0] for action in redis.actions] == ["xack", "xdel"]


async def test_worker_socket_timeout_exceeds_stream_block_window() -> None:
    client = worker._redis_client()
    try:
        assert client.connection_pool.connection_kwargs["socket_timeout"] == (
            worker.STREAM_SOCKET_TIMEOUT_SECONDS
        )
        assert worker.STREAM_SOCKET_TIMEOUT_SECONDS > worker.STREAM_BLOCK_MILLISECONDS / 1000
    finally:
        await client.aclose()
