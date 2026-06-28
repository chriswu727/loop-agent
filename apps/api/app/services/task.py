"""Task use-cases: publish, list, inspect, cancel.

Limit resolution lives here — the one place that turns a user's optional
overrides into the concrete, capped boundary the agent will obey. Clamping to the
configured caps is what makes "within the limit" a guarantee, not a suggestion.
"""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.db.models.step import StepModel
from app.db.models.task import TaskModel
from app.domain.task import TaskStatus
from app.exceptions import ConflictError, NotFoundError
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import LimitsIn, TaskCreate


class TaskService:
    def __init__(self, tasks: TaskRepository, steps: StepRepository) -> None:
        self.tasks = tasks
        self.steps = steps

    def _resolve_limits(self, limits: LimitsIn) -> tuple[int, int]:
        """Apply defaults for omitted fields, then clamp to the hard caps so no
        single task can exceed the system's ceiling."""
        max_steps = limits.max_steps or settings.agent_max_steps_default
        token_budget = limits.token_budget or settings.loop_token_budget_default

        max_steps = max(1, min(max_steps, settings.agent_max_steps_cap))
        token_budget = max(1_000, min(token_budget, settings.loop_token_budget_cap))
        return max_steps, token_budget

    async def publish(self, payload: TaskCreate) -> TaskModel:
        max_steps, token_budget = self._resolve_limits(payload.limits)
        task = await self.tasks.create(
            goal=payload.goal.strip(),
            status=TaskStatus.PENDING.value,
            rubric=[],
            max_steps=max_steps,
            token_budget=token_budget,
            summary=None,
            verification_score=0,
            steps_used=0,
            tokens_used=0,
            workspace_path=None,
        )
        # Commit before the caller schedules the run: the background/worker agent
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

    async def list_steps(self, task_id: uuid.UUID) -> list[StepModel]:
        await self.get(task_id)  # 404 if the task is unknown
        return await self.steps.list_for_task(task_id)

    async def cancel(self, task_id: uuid.UUID) -> TaskModel:
        task = await self.get(task_id)
        if task.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
            raise ConflictError(f"Task is {task.status} and cannot be cancelled")
        task.status = TaskStatus.CANCELLED.value
        await self.tasks.session.flush()
        await self.tasks.session.refresh(task)
        return task
