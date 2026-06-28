"""Task use-cases: publish, list, inspect, cancel.

Limit resolution lives here — the one place that turns a user's optional
overrides into the concrete, capped boundary the loop will obey. Clamping to the
configured caps is what makes "within the limit" a guarantee, not a suggestion.
"""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.db.models.iteration import IterationModel
from app.db.models.task import TaskModel
from app.domain.task import TaskStatus
from app.exceptions import ConflictError, NotFoundError
from app.repositories.iteration import IterationRepository
from app.repositories.task import TaskRepository
from app.schemas.task import LimitsIn, TaskCreate


class TaskService:
    def __init__(self, tasks: TaskRepository, iterations: IterationRepository) -> None:
        self.tasks = tasks
        self.iterations = iterations

    def _resolve_limits(self, limits: LimitsIn) -> tuple[int, int, int]:
        """Apply defaults for omitted fields, then clamp everything to the hard
        caps so no single task can exceed the system's ceiling."""
        max_iterations = limits.max_iterations or settings.loop_max_iterations_default
        token_budget = limits.token_budget or settings.loop_token_budget_default
        target_score = limits.target_score or settings.loop_target_score_default

        max_iterations = max(1, min(max_iterations, settings.loop_max_iterations_cap))
        token_budget = max(1_000, min(token_budget, settings.loop_token_budget_cap))
        target_score = max(1, min(target_score, 100))
        return max_iterations, token_budget, target_score

    async def publish(self, payload: TaskCreate) -> TaskModel:
        max_iterations, token_budget, target_score = self._resolve_limits(payload.limits)
        task = await self.tasks.create(
            goal=payload.goal.strip(),
            status=TaskStatus.PENDING.value,
            rubric=[],
            max_iterations=max_iterations,
            token_budget=token_budget,
            target_score=target_score,
            best_score=0,
            best_artifact=None,
            iterations_used=0,
            tokens_used=0,
        )
        # Commit before the caller schedules the run: the background/worker loop
        # opens its own session and must be able to read this row immediately.
        await self.tasks.session.commit()
        return task

    async def list(self, *, limit: int, offset: int) -> tuple[list[TaskModel], int]:
        tasks = await self.tasks.list(limit=limit, offset=offset)
        total = await self.tasks.count()
        return tasks, total

    async def get(self, task_id: uuid.UUID) -> TaskModel:
        task = await self.tasks.get(task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} does not exist")
        return task

    async def list_iterations(self, task_id: uuid.UUID) -> list[IterationModel]:
        await self.get(task_id)  # 404 if the task is unknown
        return await self.iterations.list_for_task(task_id)

    async def cancel(self, task_id: uuid.UUID) -> TaskModel:
        task = await self.get(task_id)
        if task.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
            raise ConflictError(f"Task is {task.status} and cannot be cancelled")
        task.status = TaskStatus.CANCELLED.value
        await self.tasks.session.flush()
        await self.tasks.session.refresh(task)
        return task
