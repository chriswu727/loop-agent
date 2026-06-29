"""Trigger use-cases: save a task template, then fire it to publish a task."""

from __future__ import annotations

import uuid

from app.db.models.task import TaskModel
from app.db.models.trigger import TriggerModel
from app.exceptions import ConflictError, NotFoundError
from app.repositories.trigger import TriggerRepository
from app.schemas.task import LimitsIn, TaskCreate
from app.schemas.trigger import TriggerCreate
from app.services.task import TaskService


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
        await self.triggers.session.flush()
        await self.triggers.session.refresh(trigger)
        await self.triggers.session.commit()
        return task
