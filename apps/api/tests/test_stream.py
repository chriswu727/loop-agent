"""The live snapshot the SSE endpoint streams. (The streaming transport itself is
verified by a live run; here we test the snapshot assembly deterministically.)"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.tasks import _build_snapshot
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate
from app.services.task import TaskService


async def test_build_snapshot(session: AsyncSession) -> None:
    service = TaskService(TaskRepository(session), StepRepository(session))
    task = await service.publish(TaskCreate(goal="snapshot me please"))

    snap = await _build_snapshot(service, task.id)
    assert snap.task.id == task.id
    assert snap.task.goal == "snapshot me please"
    assert snap.steps == []
    assert snap.files == []
    assert snap.ledger.length == 0
    # The whole thing serializes cleanly to the JSON the stream sends.
    assert task.goal in snap.model_dump_json()
