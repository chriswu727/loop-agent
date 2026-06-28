"""Task use-cases: publish, list, inspect, cancel.

Limit resolution lives here — the one place that turns a user's optional
overrides into the concrete, capped boundary the agent will obey. Clamping to the
configured caps is what makes "within the limit" a guarantee, not a suggestion.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings
from app.db.models.step import StepModel
from app.db.models.task import TaskModel
from app.domain.task import TaskStatus
from app.exceptions import ConflictError, NotFoundError
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import LimitsIn, TaskCreate
from app.tools.base import ToolError
from app.tools.workspace import Workspace


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

    async def _workspace(self, task_id: uuid.UUID) -> Workspace | None:
        task = await self.get(task_id)
        # Local filesystem reads on a single-node workspace; fast enough to do
        # inline without an async filesystem layer.
        if not task.workspace_path or not Path(task.workspace_path).is_dir():  # noqa: ASYNC240
            return None
        return Workspace(Path(task.workspace_path))

    async def list_files(self, task_id: uuid.UUID) -> list[tuple[str, int]]:
        ws = await self._workspace(task_id)
        return ws.list_files() if ws else []

    async def read_file(self, task_id: uuid.UUID, relpath: str) -> tuple[str, int, bool]:
        ws = await self._workspace(task_id)
        if ws is None:
            raise NotFoundError("This task has no workspace yet")
        try:
            target = ws.resolve(relpath)
        except ToolError as exc:
            raise NotFoundError(str(exc)) from exc
        if not target.is_file():
            raise NotFoundError(f"No such file: {relpath}")
        size = target.stat().st_size
        limit = 200_000
        text = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > limit
        return (text[:limit] if truncated else text), size, truncated

    async def resolve_file(self, task_id: uuid.UUID, relpath: str) -> Path:
        ws = await self._workspace(task_id)
        if ws is None:
            raise NotFoundError("This task has no workspace yet")
        try:
            target = ws.resolve(relpath)
        except ToolError as exc:
            raise NotFoundError(str(exc)) from exc
        if not target.is_file():
            raise NotFoundError(f"No such file: {relpath}")
        return target

    async def cancel(self, task_id: uuid.UUID) -> TaskModel:
        task = await self.get(task_id)
        active = (
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.AWAITING_INPUT.value,
        )
        if task.status not in active:
            raise ConflictError(f"Task is {task.status} and cannot be cancelled")
        task.status = TaskStatus.CANCELLED.value
        await self.tasks.session.flush()
        await self.tasks.session.refresh(task)
        return task

    async def respond(self, task_id: uuid.UUID, answer: str) -> TaskModel:
        """Record the user's answer to an ask_user question and mark the task
        resumable. The caller schedules the resume after the commit."""
        task = await self.get(task_id)
        if task.status != TaskStatus.AWAITING_INPUT.value:
            raise ConflictError(f"Task is {task.status} and is not awaiting input")
        steps = await self.steps.list_for_task(task_id)
        ask_steps = [s for s in steps if s.tool == "ask_user"]
        if ask_steps:
            ask_steps[-1].observation = (
                f"You asked: {task.pending_question}\nUser answered: {answer}"
            )
        task.pending_question = None
        task.status = TaskStatus.PENDING.value  # pending == ready to (re)run
        # flush+refresh pulls the server-side onupdate ``updated_at`` before it is
        # serialized (otherwise it lazy-loads in a sync context and 500s). Commit
        # before the caller schedules the resume so the agent's own session sees
        # the answer and the updated status.
        await self.tasks.session.flush()
        await self.tasks.session.refresh(task)
        await self.tasks.session.commit()
        return task
