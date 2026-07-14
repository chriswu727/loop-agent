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
