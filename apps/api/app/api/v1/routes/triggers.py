"""HTTP surface for triggers — saved task templates fired by external events."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Header, Query, status

from app.api.v1.deps import TriggerServiceDep
from app.schemas.task import TaskRead
from app.schemas.trigger import TriggerCreate, TriggerRead
from app.services.runner import trigger_task

router = APIRouter(prefix="/triggers", tags=["triggers"])


@router.get("", response_model=list[TriggerRead], summary="List triggers")
async def list_triggers(service: TriggerServiceDep) -> list[TriggerRead]:
    return [TriggerRead.model_validate(t) for t in await service.list()]


@router.post(
    "", response_model=TriggerRead, status_code=status.HTTP_201_CREATED, summary="Create a trigger"
)
async def create_trigger(payload: TriggerCreate, service: TriggerServiceDep) -> TriggerRead:
    return TriggerRead.model_validate(await service.create(payload))


@router.delete(
    "/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a trigger"
)
async def delete_trigger(trigger_id: uuid.UUID, service: TriggerServiceDep) -> None:
    await service.delete(trigger_id)


@router.post(
    "/{trigger_id}/fire",
    response_model=TaskRead,
    summary="Fire a trigger (publishes and starts a task)",
)
async def fire_trigger(
    trigger_id: uuid.UUID,
    service: TriggerServiceDep,
    background: BackgroundTasks,
    x_trigger_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> TaskRead:
    # The secret may come from a header (preferred) or a query param (for webhook
    # senders that only support a URL). Either must match the trigger's secret.
    task = await service.fire_via_webhook(trigger_id, x_trigger_secret or secret)
    background.add_task(trigger_task, task.id)  # run after the response/commit
    return TaskRead.from_model(task)
