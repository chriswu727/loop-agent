"""Trigger use-cases: save a task template, then fire it to publish a task."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.db.models.task import TaskModel
from app.db.models.trigger import TriggerModel
from app.exceptions import ConflictError, NotFoundError
from app.repositories.trigger import TriggerRepository
from app.schemas.task import LimitsIn, TaskCreate
from app.schemas.trigger import TriggerCreate
from app.services.task import TaskService

log = get_logger("scheduler")


def _now() -> datetime:
    # naive UTC, compared consistently across SQLite (naive) and Postgres
    return datetime.now(UTC).replace(tzinfo=None)


def _is_due(trigger: TriggerModel, now: datetime) -> bool:
    if not trigger.enabled or trigger.interval_minutes is None:
        return False
    if trigger.last_fired_at is None:
        return True
    last = trigger.last_fired_at.replace(tzinfo=None)
    return (now - last).total_seconds() >= trigger.interval_minutes * 60


class TriggerService:
    def __init__(self, triggers: TriggerRepository, task_service: TaskService) -> None:
        self.triggers = triggers
        self.task_service = task_service

    async def create(self, payload: TriggerCreate) -> TriggerModel:
        max_steps, token_budget = self.task_service._resolve_limits(payload.limits)
        return await self.triggers.create(
            name=payload.name.strip(),
            goal=payload.goal.strip(),
            enabled=True,
            fire_count=0,
            max_steps=max_steps,
            token_budget=token_budget,
            allowed_tools=payload.allowed_tools,
            allow_egress=payload.allow_egress,
            require_approval=payload.require_approval,
            skill=payload.skill,
            interval_minutes=payload.interval_minutes,
            last_fired_at=None,
        )

    async def list(self) -> list[TriggerModel]:
        return await self.triggers.list(limit=100, offset=0)

    async def get(self, trigger_id: uuid.UUID) -> TriggerModel:
        trigger = await self.triggers.get(trigger_id)
        if trigger is None:
            raise NotFoundError(f"Trigger {trigger_id} does not exist")
        return trigger

    async def delete(self, trigger_id: uuid.UUID) -> None:
        deleted = await self.triggers.delete(trigger_id)
        if deleted == 0:
            raise NotFoundError(f"Trigger {trigger_id} does not exist")

    async def fire(self, trigger_id: uuid.UUID) -> TaskModel:
        """Publish a task from the trigger's template and bump its fire count."""
        trigger = await self.get(trigger_id)
        if not trigger.enabled:
            raise ConflictError("Trigger is disabled")
        task = await self.task_service.publish(
            TaskCreate(
                goal=trigger.goal,
                limits=LimitsIn(max_steps=trigger.max_steps, token_budget=trigger.token_budget),
                allowed_tools=trigger.allowed_tools,
                allow_egress=trigger.allow_egress,
                require_approval=trigger.require_approval,
                skill=trigger.skill,
            )
        )
        trigger.fire_count += 1
        trigger.last_fired_at = _now()
        await self.triggers.session.flush()
        await self.triggers.session.refresh(trigger)
        await self.triggers.session.commit()
        return task

    async def tick(self) -> int:
        """Fire every trigger whose interval is due and start its task. Returns the
        number fired. Called periodically by the scheduler loop."""
        from app.services.runner import trigger_task

        now = _now()
        due = [t for t in await self.list() if _is_due(t, now)]
        for trigger in due:
            task = await self.fire(trigger.id)
            await trigger_task(task.id)
            log.info("scheduler.fired", trigger=trigger.name, task_id=str(task.id))
        return len(due)
