"""Sub-agents (spawned children) are excluded from the top-level list and
retrievable under their parent."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.task import TaskService


async def test_root_filter_and_children(session: AsyncSession) -> None:
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
    parent = await repo.create(goal="parent", status="pending", **common)
    await session.commit()
    child = await repo.create(
        goal="child", status="completed", parent_id=parent.id, depth=1, **common
    )
    await session.commit()

    svc = TaskService(repo, StepRepository(session))
    roots, total = await svc.list_tasks(limit=50, offset=0, root_only=True)
    assert total == 1 and roots[0].id == parent.id  # child excluded from the list

    _all, total_all = await svc.list_tasks(limit=50, offset=0, root_only=False)
    assert total_all == 2

    children = await svc.list_children(parent.id)
    assert len(children) == 1 and children[0].id == child.id
