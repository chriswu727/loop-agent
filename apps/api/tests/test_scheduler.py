"""The heartbeat fires interval triggers when due, and only when due."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trigger import TriggerModel
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.repositories.trigger import TriggerRepository
from app.schemas.trigger import TriggerCreate
from app.services.task import TaskService
from app.services.trigger import TriggerService, _is_due


def _t(**kw: object) -> TriggerModel:
    base = {"enabled": True, "interval_minutes": 5, "last_fired_at": None}
    return TriggerModel(**{**base, **kw})  # type: ignore[arg-type]


def test_due_logic() -> None:
    now = datetime(2026, 6, 28, 12, 0, 0)
    assert _is_due(_t(last_fired_at=None), now) is True  # never fired
    assert _is_due(_t(interval_minutes=None), now) is False  # manual only
    assert _is_due(_t(enabled=False), now) is False  # disabled
    assert _is_due(_t(last_fired_at=now - timedelta(minutes=2)), now) is False  # too soon
    assert _is_due(_t(last_fired_at=now - timedelta(minutes=6)), now) is True  # elapsed


async def test_tick_fires_due_trigger(session: AsyncSession, monkeypatch) -> None:
    async def _noop(task_id: object) -> None:
        return None

    monkeypatch.setattr("app.services.runner.trigger_task", _noop, raising=True)

    service = TriggerService(
        TriggerRepository(session),
        TaskService(TaskRepository(session), StepRepository(session)),
    )
    trigger = await service.create(
        TriggerCreate(name="hb", goal="do the periodic thing", interval_minutes=1)
    )
    await session.commit()

    fired = await service.tick()
    assert fired == 1

    # A task was published and the trigger's state advanced.
    svc = TaskService(TaskRepository(session), StepRepository(session))
    _published, total = await svc.list_tasks(limit=10, offset=0)
    assert total == 1
    await session.refresh(trigger)
    assert trigger.fire_count == 1
    assert trigger.last_fired_at is not None

    # A second immediate tick does not re-fire (interval not yet elapsed).
    assert await service.tick() == 0


async def test_reconcile_fails_interrupted_running_tasks(session: AsyncSession) -> None:
    from app.domain.task import StopReason, TaskStatus
    from app.services.runner import reconcile_interrupted_tasks

    repo = TaskRepository(session)
    common = dict(  # noqa: C408
        rubric=[],
        max_steps=5,
        token_budget=1000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    running = await repo.create(goal="was mid-run", status=TaskStatus.RUNNING.value, **common)
    paused = await repo.create(goal="paused", status=TaskStatus.AWAITING_INPUT.value, **common)
    await session.commit()

    assert await reconcile_interrupted_tasks(session) == 1
    await session.refresh(running)
    await session.refresh(paused)
    assert running.status == TaskStatus.FAILED.value
    assert running.stop_reason == StopReason.ERROR.value
    assert paused.status == TaskStatus.AWAITING_INPUT.value  # untouched — still resumable
