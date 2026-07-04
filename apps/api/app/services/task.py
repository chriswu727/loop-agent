"""Task use-cases: publish, list, inspect, cancel.

Limit resolution lives here — the one place that turns a user's optional
overrides into the concrete, capped boundary the agent will obey. Clamping to the
configured caps is what makes "within the limit" a guarantee, not a suggestion.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.models.step import StepModel
from app.db.models.task import TaskModel
from app.domain.task import TaskStatus
from app.exceptions import ConflictError, NotFoundError
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import LimitsIn, TaskCreate
from app.services.ledger import genesis_hash, step_hash, verify_chain
from app.tools.base import ToolError
from app.tools.workspace import Workspace

_APPROVAL_WORDS = frozenset(
    {
        "yes",
        "y",
        "approve",
        "approved",
        "ok",
        "okay",
        "allow",
        "sure",
        "proceed",
        "是",
        "好",
        "可以",
        "批准",
        "同意",
        "允许",
    }
)


def _is_approval(answer: str) -> bool:
    """Interpret a free-text answer to an approval prompt. Default: deny."""
    token = answer.strip().lower().split()[0] if answer.strip() else ""
    return token in _APPROVAL_WORDS


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
            allowed_tools=payload.allowed_tools,
            allow_egress=payload.allow_egress,
            egress_hosts=payload.egress_hosts,
            require_approval=payload.require_approval,
            use_browser=payload.use_browser,
            use_email=payload.use_email,
            use_calendar=payload.use_calendar,
            chat_id=payload.chat_id,
            skill=payload.skill,
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

    async def retry(self, task_id: uuid.UUID) -> TaskModel:
        """Re-run a finished task's goal as a fresh task with the same settings.
        The original stays as-is, so its Receipt/ledger remain an audit record."""
        original = await self.get(task_id)
        if original.status not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            raise ConflictError(f"Task is {original.status}; only a finished task can be retried.")
        # Re-clamp to the caps in force at retry time — the only creation path that
        # would otherwise skip _resolve_limits and could exceed a lowered ceiling.
        max_steps, token_budget = self._resolve_limits(
            LimitsIn(max_steps=original.max_steps, token_budget=original.token_budget)
        )
        task = await self.tasks.create(
            goal=original.goal,
            status=TaskStatus.PENDING.value,
            rubric=[],
            allowed_tools=original.allowed_tools,
            allow_egress=original.allow_egress,
            egress_hosts=original.egress_hosts,
            require_approval=original.require_approval,
            use_browser=original.use_browser,
            use_email=original.use_email,
            use_calendar=original.use_calendar,
            chat_id=original.chat_id,
            skill=original.skill,
            max_steps=max_steps,
            token_budget=token_budget,
            summary=None,
            verification_score=0,
            steps_used=0,
            tokens_used=0,
            workspace_path=None,
        )
        await self.tasks.session.commit()
        return task

    async def list_tasks(
        self, *, limit: int, offset: int, root_only: bool = True
    ) -> tuple[list[TaskModel], int]:
        if root_only:
            tasks = await self.tasks.list_roots(limit=limit, offset=offset)
            total = await self.tasks.count_roots()
        else:
            tasks = await self.tasks.list(limit=limit, offset=offset)
            total = await self.tasks.count()
        return tasks, total

    async def list_children(self, task_id: uuid.UUID) -> list[TaskModel]:
        await self.get(task_id)  # 404 if unknown
        return await self.tasks.list_children(task_id)

    async def get(self, task_id: uuid.UUID) -> TaskModel:
        task = await self.tasks.get(task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} does not exist")
        return task

    async def list_steps(self, task_id: uuid.UUID) -> list[StepModel]:
        await self.get(task_id)  # 404 if the task is unknown
        return await self.steps.list_for_task(task_id)

    async def verify_ledger(
        self, task_id: uuid.UUID, steps: list[StepModel] | None = None
    ) -> dict[str, object]:
        """Re-verify the tamper-evident step chain. Callers that already have the
        steps (e.g. the snapshot builder) pass them in to avoid a second fetch."""
        if steps is None:
            steps = await self.list_steps(task_id)
        ok, broken_at = verify_chain(task_id, steps)
        head = steps[-1].hash if steps else genesis_hash(task_id)
        return {"verified": ok, "head": head, "length": len(steps), "broken_at": broken_at}

    async def _workspace(self, task_id: uuid.UUID) -> Workspace | None:
        task = await self.get(task_id)
        # Local filesystem reads on a single-node workspace; fast enough to do
        # inline without an async filesystem layer.
        if not task.workspace_path or not Path(task.workspace_path).is_dir():  # noqa: ASYNC240
            return None
        return Workspace(Path(task.workspace_path))

    async def ensure_workspace(self, task_id: uuid.UUID) -> Workspace:
        """Create the task's workspace if it doesn't exist yet, so files can be
        uploaded before the agent runs. The agent run reuses this same path."""
        task = await self.get(task_id)
        if task.workspace_path and Path(task.workspace_path).is_dir():  # noqa: ASYNC240
            return Workspace(Path(task.workspace_path))
        workspace = Workspace(Path(settings.agent_workspaces_root) / str(task.id))
        task.workspace_path = str(workspace.root)
        await self.tasks.session.flush()
        await self.tasks.session.refresh(task)
        await self.tasks.session.commit()
        return workspace

    async def save_upload(self, task_id: uuid.UUID, filename: str, data: bytes) -> str:
        """Write an uploaded file into the task workspace (sandbox-checked)."""
        workspace = await self.ensure_workspace(task_id)
        # Use only the basename so an upload can't path-escape via its name.
        safe_name = Path(filename or "upload.bin").name
        target = workspace.resolve(safe_name)
        target.write_bytes(data)
        return safe_name

    async def start(self, task_id: uuid.UUID) -> TaskModel:
        """Begin a draft task (one published with autostart=false)."""
        task = await self.get(task_id)
        if task.status != TaskStatus.PENDING.value or task.steps_used > 0:
            raise ConflictError(f"Task is {task.status} and cannot be started")
        return task

    async def list_files(self, task_id: uuid.UUID) -> list[tuple[str, int]]:
        ws = await self._workspace(task_id)
        return ws.list_files() if ws else []

    async def get_receipt(self, task_id: uuid.UUID) -> dict[str, Any] | None:
        """The parsed receipt.json from the task's workspace, or None if absent."""
        ws = await self._workspace(task_id)
        if ws is None:
            return None
        try:
            content = ws.read("receipt.json", limit=500_000)
            data: dict[str, Any] = json.loads(content)
            return data
        except (FileNotFoundError, ValueError, OSError):
            return None

    async def get_receipt_report(self, task_id: uuid.UUID) -> dict[str, Any] | None:
        """The Receipt plus a layered re-verification: content hash, signature, the
        independent DB hash anchor, and a re-hash of every output file against the
        manifest — so tampering with the file *or* the receipt is caught, not just
        an internally-inconsistent edit."""
        from app.services.receipt import verify_receipt_full

        receipt = await self.get_receipt(task_id)
        if receipt is None:
            return None
        task = await self.get(task_id)
        ws = await self._workspace(task_id)
        report = verify_receipt_full(receipt, workspace=ws, db_anchor=task.receipt_hash)
        return {"receipt": receipt, **report}

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
        """Record the user's answer (to an ask_user question, or an approval
        decision) and mark the task resumable. The caller schedules the resume."""
        task = await self.get(task_id)
        if task.status != TaskStatus.AWAITING_INPUT.value:
            raise ConflictError(f"Task is {task.status} and is not awaiting input")
        steps = await self.steps.list_for_task(task_id)

        edited = False
        if task.pending_action is not None:
            # Approval gate: yes/no decides whether the pending action runs.
            approved = _is_approval(answer)
            if steps:
                steps[-1].observation += f"\nUser {'approved' if approved else 'denied'}: {answer}"
                edited = True
            if not approved:
                task.pending_action = None  # denied -> the action is dropped
        elif steps:
            steps[-1].observation = f"You asked: {task.pending_question}\nUser answered: {answer}"
            edited = True

        # Recording the answer changes the last step's observation, which is part
        # of its ledger hash — re-seal it so the tamper-evident chain stays valid.
        # It's the last step, so nothing downstream needs re-chaining.
        if edited:
            last = steps[-1]
            last.hash = step_hash(
                last.prev_hash or "",
                number=last.number,
                tool=last.tool,
                tool_args=last.tool_args,
                observation=last.observation,
                status=last.status,
                tokens=last.tokens,
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
