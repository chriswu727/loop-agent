"""Trigger use-cases: save a task template, then fire it to publish a task."""

from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.db.models.task import TaskModel
from app.db.models.trigger import TriggerModel
from app.domain.capability import parse_capabilities, sorted_capabilities
from app.exceptions import ConflictError, ForbiddenError, NotFoundError
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
    def __init__(
        self,
        triggers: TriggerRepository,
        task_service: TaskService,
        *,
        subject: str | None = "from-task-service",
    ) -> None:
        self.triggers = triggers
        self.task_service = task_service
        self.subject = task_service.subject if subject == "from-task-service" else subject

    async def create(self, payload: TriggerCreate) -> TriggerModel:
        max_steps, token_budget = self.task_service._resolve_limits(payload.limits)
        return await self.triggers.create(
            name=payload.name.strip(),
            goal=payload.goal.strip(),
            owner_id=self.task_service.subject,
            project_id=payload.project_id,
            enabled=True,
            fire_count=0,
            max_steps=max_steps,
            token_budget=token_budget,
            secret=secrets.token_urlsafe(24),
            allowed_tools=payload.allowed_tools,
            capabilities=(
                sorted_capabilities(payload.capabilities)
                if payload.capabilities is not None
                else None
            ),
            allow_egress=payload.allow_egress,
            require_approval=payload.require_approval,
            skill=payload.skill,
            interval_minutes=payload.interval_minutes,
            last_fired_at=None,
        )

    async def list(self) -> list[TriggerModel]:
        if self.subject is None:
            return await self.triggers.list(limit=100, offset=0)
        return await self.triggers.list_for_owner(self.subject)

    async def get(self, trigger_id: uuid.UUID) -> TriggerModel:
        trigger = await self.triggers.get(trigger_id)
        if trigger is None or (self.subject is not None and trigger.owner_id != self.subject):
            raise NotFoundError(f"Trigger {trigger_id} does not exist")
        return trigger

    async def delete(self, trigger_id: uuid.UUID) -> None:
        await self.get(trigger_id)
        deleted = await self.triggers.delete(trigger_id)
        if deleted == 0:
            raise NotFoundError(f"Trigger {trigger_id} does not exist")

    async def fire_via_webhook(
        self, trigger_id: uuid.UUID, provided_secret: str | None
    ) -> TaskModel:
        """Fire from an external HTTP caller — the trigger's secret must match.
        (The scheduler's own ticks call ``fire`` directly and skip this check.)"""
        trigger = await self.get(trigger_id)
        if not provided_secret or not hmac.compare_digest(provided_secret, trigger.secret):
            raise ForbiddenError("Invalid or missing trigger secret.")
        return await self.fire(trigger_id)

    async def fire(self, trigger_id: uuid.UUID) -> TaskModel:
        """Publish a task from the trigger's template and bump its fire count."""
        trigger = await self.get(trigger_id)
        if not trigger.enabled:
            raise ConflictError("Trigger is disabled")
        claimed = await self.triggers.claim_fire(
            trigger.id,
            expected_last_fired_at=trigger.last_fired_at,
            now=_now(),
        )
        if claimed is None:
            raise ConflictError("Trigger was already fired by another scheduler or request")
        owner_tasks = TaskService(
            self.task_service.tasks,
            self.task_service.steps,
            subject=claimed.owner_id,
        )
        task = await owner_tasks.publish(
            TaskCreate(
                goal=claimed.goal,
                project_id=claimed.project_id,
                limits=LimitsIn(max_steps=claimed.max_steps, token_budget=claimed.token_budget),
                allowed_tools=claimed.allowed_tools,
                capabilities=(
                    list(parse_capabilities(claimed.capabilities))
                    if claimed.capabilities is not None
                    else None
                ),
                allow_egress=claimed.allow_egress,
                require_approval=claimed.require_approval,
                skill=claimed.skill,
                idempotency_key=f"trigger:{claimed.id}:{claimed.fire_count}",
            )
        )
        return task

    async def tick(self) -> int:
        """Fire every trigger whose interval is due and start its task. Returns the
        number fired. Called periodically by the scheduler loop."""
        from app.services.runner import trigger_task

        now = _now()
        due = [t for t in await self.list() if _is_due(t, now)]
        fired = 0
        for trigger in due:
            try:
                task = await self.fire(trigger.id)
            except ConflictError:
                continue
            await trigger_task(task.id)
            log.info("scheduler.fired", trigger=trigger.name, task_id=str(task.id))
            fired += 1
        return fired
