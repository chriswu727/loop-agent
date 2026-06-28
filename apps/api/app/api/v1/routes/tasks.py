"""HTTP surface for the agent loop.

Thin transport: validate, call the service, map to DTOs. The one bit of
orchestration here is scheduling the run *after* the request commits, via a
background task, so the loop never sees a half-written row.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Query, status

from app.api.v1.deps import TaskServiceDep, rate_limit
from app.schemas.common import Page
from app.schemas.step import StepRead
from app.schemas.task import LimitDefaults, TaskCreate, TaskRead
from app.services.runner import trigger_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/limits", response_model=LimitDefaults, summary="Default + cap limits")
async def get_limit_defaults() -> LimitDefaults:
    """Let the publish form render controls seeded with sane defaults and caps."""
    return LimitDefaults()


@router.get("", response_model=Page[TaskRead], summary="List tasks")
async def list_tasks(
    service: TaskServiceDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Page[TaskRead]:
    tasks, total = await service.list(limit=limit, offset=offset)
    return Page[TaskRead](
        items=[TaskRead.from_model(t) for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a task (starts the loop)",
    dependencies=[rate_limit(limit=20, window_seconds=60)],
)
async def publish_task(
    payload: TaskCreate, service: TaskServiceDep, background: BackgroundTasks
) -> TaskRead:
    task = await service.publish(payload)
    # Runs after the response (and the request commit), so the loop's own
    # session reliably finds the row.
    background.add_task(trigger_task, task.id)
    return TaskRead.from_model(task)


@router.get("/{task_id}", response_model=TaskRead, summary="Get a task")
async def get_task(task_id: uuid.UUID, service: TaskServiceDep) -> TaskRead:
    task = await service.get(task_id)
    return TaskRead.from_model(task)


@router.get(
    "/{task_id}/steps",
    response_model=list[StepRead],
    summary="List a task's agent steps",
)
async def list_steps(task_id: uuid.UUID, service: TaskServiceDep) -> list[StepRead]:
    steps = await service.list_steps(task_id)
    return [StepRead.model_validate(s) for s in steps]


@router.post("/{task_id}/cancel", response_model=TaskRead, summary="Cancel a running task")
async def cancel_task(task_id: uuid.UUID, service: TaskServiceDep) -> TaskRead:
    task = await service.cancel(task_id)
    return TaskRead.from_model(task)
