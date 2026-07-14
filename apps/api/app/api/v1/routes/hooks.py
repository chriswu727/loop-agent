"""Externally callable trigger webhooks authenticated by a per-trigger secret."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Header, Query

from app.api.v1.deps import SessionDep
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.repositories.trigger import TriggerRepository
from app.schemas.task import TaskRead
from app.services.runner import trigger_task
from app.services.task import TaskService
from app.services.trigger import TriggerService

router = APIRouter(prefix="/hooks/triggers", tags=["hooks"])


@router.post("/{trigger_id}", response_model=TaskRead, summary="Fire a trigger webhook")
async def fire_trigger_webhook(
    trigger_id: uuid.UUID,
    session: SessionDep,
    background: BackgroundTasks,
    x_trigger_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> TaskRead:
    service = TriggerService(
        TriggerRepository(session),
        TaskService(TaskRepository(session), StepRepository(session)),
        subject=None,
    )
    task = await service.fire_via_webhook(trigger_id, x_trigger_secret or secret)
    background.add_task(trigger_task, task.id)
    return TaskRead.from_model(task)
