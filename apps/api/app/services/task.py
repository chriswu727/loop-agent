"""Task use-cases: publish, list, inspect, cancel.

Limit resolution lives here — the one place that turns a user's optional
overrides into the concrete, capped boundary the agent will obey. Clamping to the
configured caps is what makes "within the limit" a guarantee, not a suggestion.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models.step import StepModel
from app.db.models.task import TaskModel
from app.domain.capability import sorted_capabilities
from app.domain.task import StopReason, TaskStatus
from app.exceptions import ConflictError, NotFoundError
from app.observability.metrics import RECEIPT_REPLAYS
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import ChangeSetFileRead, ChangeSetRead, LimitsIn, TaskCreate
from app.services.changeset import (
    ProjectBinding,
    acquire_source_lock,
    apply_patch,
    clone_project,
    delete_patch,
    inspect_changes,
    load_patch,
    prepare_project,
    release_source_lock,
    save_patch,
)
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
    def __init__(
        self, tasks: TaskRepository, steps: StepRepository, *, subject: str = "local"
    ) -> None:
        self.tasks = tasks
        self.steps = steps
        self.subject = subject

    def _resolve_limits(self, limits: LimitsIn) -> tuple[int, int]:
        """Apply defaults for omitted fields, then clamp to the hard caps so no
        single task can exceed the system's ceiling."""
        max_steps = limits.max_steps or settings.agent_max_steps_default
        token_budget = limits.token_budget or settings.loop_token_budget_default

        max_steps = max(1, min(max_steps, settings.agent_max_steps_cap))
        token_budget = max(1_000, min(token_budget, settings.loop_token_budget_cap))
        return max_steps, token_budget

    async def publish(self, payload: TaskCreate) -> TaskModel:
        if payload.idempotency_key:
            existing = await self.tasks.get_by_idempotency_key(
                payload.idempotency_key, owner_id=self.subject
            )
            if existing is not None:
                return existing
        binding = (
            await asyncio.to_thread(prepare_project, payload.project_path)
            if payload.project_path
            else None
        )
        max_steps, token_budget = self._resolve_limits(payload.limits)
        criteria = list(payload.success_criteria or [])
        verification_mode = payload.verification_mode or (
            "strict" if binding is not None or criteria else "judgment"
        )
        required_checks = [
            {
                "id": f"contract-{index:03d}",
                "kind": "command",
                "command": command,
                "expect_exit": 0,
                "source": "contract",
            }
            for index, command in enumerate(payload.verification_commands, start=1)
        ]
        task = await self.tasks.create(
            goal=payload.goal.strip(),
            owner_id=self.subject,
            project_id=payload.project_id,
            status=TaskStatus.PENDING.value,
            rubric=criteria,
            criteria_source="user" if criteria else "generated",
            verification_mode=verification_mode,
            required_checks=required_checks,
            baseline_checks=[],
            requested_capabilities=(
                sorted_capabilities(payload.capabilities)
                if payload.capabilities is not None
                else None
            ),
            resolved_capabilities=[],
            allowed_tools=payload.allowed_tools,
            allow_egress=payload.allow_egress,
            egress_hosts=payload.egress_hosts,
            require_approval=payload.require_approval,
            use_browser=payload.use_browser,
            use_email=payload.use_email,
            use_calendar=payload.use_calendar,
            use_vision=payload.use_vision,
            chat_id=payload.chat_id,
            skill=payload.skill,
            idempotency_key=payload.idempotency_key,
            max_steps=max_steps,
            token_budget=token_budget,
            summary=None,
            verification_score=0,
            executor_models=[],
            verifier_model=None,
            authority_audit=[],
            steps_used=0,
            tokens_used=0,
            workspace_path=None,
            project_source_path=str(binding.source) if binding else None,
            project_relative_path=binding.relative_path if binding else None,
            project_base_commit=binding.base_commit if binding else None,
            project_base_branch=binding.branch if binding else None,
            change_state="pending" if binding else None,
            applied_patch_sha256=None,
        )
        if binding is not None:
            try:
                await self._attach_project(task, binding)
            except IntegrityError:
                if not payload.idempotency_key:
                    raise
                existing = await self.tasks.get_by_idempotency_key(
                    payload.idempotency_key, owner_id=self.subject
                )
                if existing is None:
                    raise
                return existing
            return task
        # Commit before the caller schedules the run: the background/worker agent
        # opens its own session and must be able to read this row immediately.
        try:
            await self.tasks.session.commit()
        except IntegrityError:
            await self.tasks.session.rollback()
            if not payload.idempotency_key:
                raise
            existing = await self.tasks.get_by_idempotency_key(
                payload.idempotency_key, owner_id=self.subject
            )
            if existing is None:
                raise
            return existing
        return task

    async def _attach_project(self, task: TaskModel, binding: ProjectBinding) -> None:
        destination = Path(settings.agent_workspaces_root) / str(task.id)
        try:
            await self.tasks.session.flush()
            await asyncio.to_thread(clone_project, binding, destination)
            task.workspace_path = str(destination.resolve())
            await self.tasks.session.commit()
            await self.tasks.session.refresh(task)
        except Exception:
            await self.tasks.session.rollback()
            await asyncio.to_thread(shutil.rmtree, destination, ignore_errors=True)
            raise

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
        binding = (
            await asyncio.to_thread(prepare_project, original.project_relative_path)
            if original.project_relative_path
            else None
        )
        # Re-clamp to the caps in force at retry time — the only creation path that
        # would otherwise skip _resolve_limits and could exceed a lowered ceiling.
        max_steps, token_budget = self._resolve_limits(
            LimitsIn(max_steps=original.max_steps, token_budget=original.token_budget)
        )
        task = await self.tasks.create(
            goal=original.goal,
            owner_id=self.subject,
            project_id=original.project_id,
            status=TaskStatus.PENDING.value,
            rubric=(list(original.rubric or []) if original.criteria_source == "user" else []),
            criteria_source=original.criteria_source,
            verification_mode=original.verification_mode,
            required_checks=[
                check
                for check in (original.required_checks or [])
                if check.get("source") == "contract"
            ],
            baseline_checks=[],
            requested_capabilities=original.requested_capabilities,
            resolved_capabilities=[],
            allowed_tools=original.allowed_tools,
            allow_egress=original.allow_egress,
            egress_hosts=original.egress_hosts,
            require_approval=original.require_approval,
            use_browser=original.use_browser,
            use_email=original.use_email,
            use_calendar=original.use_calendar,
            use_vision=original.use_vision,
            chat_id=original.chat_id,
            skill=original.skill,
            idempotency_key=None,
            attempt=original.attempt + 1,
            max_steps=max_steps,
            token_budget=token_budget,
            summary=None,
            verification_score=0,
            executor_models=[],
            verifier_model=None,
            steps_used=0,
            tokens_used=0,
            workspace_path=None,
            project_source_path=str(binding.source) if binding else None,
            project_relative_path=binding.relative_path if binding else None,
            project_base_commit=binding.base_commit if binding else None,
            project_base_branch=binding.branch if binding else None,
            change_state="pending" if binding else None,
            applied_patch_sha256=None,
        )
        if binding is not None:
            await self._attach_project(task, binding)
        else:
            await self.tasks.session.commit()
        return task

    async def list_tasks(
        self, *, limit: int, offset: int, root_only: bool = True
    ) -> tuple[list[TaskModel], int]:
        if root_only:
            tasks = await self.tasks.list_roots(limit=limit, offset=offset, owner_id=self.subject)
            total = await self.tasks.count_roots(owner_id=self.subject)
        else:
            tasks = await self.tasks.list_for_owner(self.subject, limit=limit, offset=offset)
            total = await self.tasks.count_for_owner(self.subject)
        return tasks, total

    async def list_children(self, task_id: uuid.UUID) -> list[TaskModel]:
        await self.get(task_id)  # 404 if unknown
        return await self.tasks.list_children(task_id)

    async def get(self, task_id: uuid.UUID) -> TaskModel:
        task = await self.tasks.get(task_id)
        if task is None or task.owner_id != self.subject:
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
        task = await self.get(task_id)
        if task.status != TaskStatus.PENDING.value or task.steps_used > 0:
            raise ConflictError("Files can only be uploaded before a task starts.")
        workspace = await self.ensure_workspace(task_id)
        # Use only the basename so an upload can't path-escape via its name.
        safe_name = Path(filename or "upload.bin").name
        target = workspace.resolve(safe_name)
        existing_size = target.stat().st_size if target.is_file() else 0
        current_size = sum(size for _, size in workspace.list_files())
        if current_size - existing_size + len(data) > settings.agent_max_workspace_bytes:
            raise ConflictError("Upload would exceed the task workspace quota.")
        target.write_bytes(data)
        return safe_name

    async def start(self, task_id: uuid.UUID) -> TaskModel:
        """Begin a draft task (one published with autostart=false)."""
        task = await self.get(task_id)
        if task.status != TaskStatus.PENDING.value or task.steps_used > 0:
            raise ConflictError(f"Task is {task.status} and cannot be started")
        return task

    async def list_files(self, task_id: uuid.UUID) -> list[tuple[str, int]]:
        task = await self.get(task_id)
        ws = await self._workspace(task_id)
        if ws is None:
            return []
        if not task.project_base_commit:
            return ws.list_files()
        snapshot = await asyncio.to_thread(
            inspect_changes, Path(task.workspace_path or ""), task.project_base_commit
        )
        paths = [change.path for change in snapshot.files]
        paths.extend(path for path in ("receipt.json", "RECEIPT.md") if (ws.root / path).is_file())
        files: list[tuple[str, int]] = []
        for path in dict.fromkeys(paths):
            try:
                target = ws.resolve(path)
            except ToolError:
                continue
            if target.is_file():
                files.append((path, target.stat().st_size))
        return files

    async def inspect_change_set(self, task_id: uuid.UUID) -> ChangeSetRead:
        task = await self.get(task_id)
        if not task.project_source_path or not task.project_base_commit or not task.workspace_path:
            raise NotFoundError("This task is not bound to a local Git project.")
        base_commit = task.project_base_commit
        snapshot = await asyncio.to_thread(inspect_changes, Path(task.workspace_path), base_commit)
        state = task.change_state or "pending"
        blocked_reason: str | None = None
        if not snapshot.files:
            blocked_reason = "The isolated checkout has no project changes."
        elif task.status != TaskStatus.COMPLETED.value or task.stop_reason != (
            StopReason.GOAL_ACHIEVED.value
        ):
            blocked_reason = "Apply is available only after the task completes successfully."
        elif task.verified_by != "execution" or task.verification_score < (
            settings.agent_acceptance_score
        ):
            blocked_reason = "Apply requires execution-backed verification."
        elif state in {"pending", "reverted"}:
            report = await self.get_receipt_report(task_id)
            receipt_change_set = report["receipt"].get("change_set") if report else None
            receipt_coverage = report["receipt"].get("coverage") if report else None
            expected_criteria = {
                f"criterion-{index:03d}" for index in range(1, len(task.rubric or []) + 1)
            }
            covered_criteria = (
                {
                    value
                    for value in receipt_coverage.get("covered_criteria", [])
                    if isinstance(value, str)
                }
                if isinstance(receipt_coverage, dict)
                else set()
            )
            if not report or not report.get("valid"):
                blocked_reason = "Receipt integrity verification failed."
            elif (
                not isinstance(receipt_coverage, dict)
                or receipt_coverage.get("execution_backed") is not True
                or not expected_criteria <= covered_criteria
            ):
                blocked_reason = "Receipt does not prove every acceptance criterion."
            elif (
                not isinstance(receipt_change_set, dict)
                or receipt_change_set.get("patch_sha256") != snapshot.patch_sha256
            ):
                blocked_reason = "The current diff no longer matches the verified Receipt."

        terminal = task.status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.FAILED.value,
        }
        return ChangeSetRead(
            project_path=task.project_relative_path or ".",
            base_commit=task.project_base_commit,
            base_branch=task.project_base_branch,
            state=state,
            applied_patch_sha256=task.applied_patch_sha256,
            patch_sha256=snapshot.patch_sha256,
            files=[
                ChangeSetFileRead(
                    path=change.path,
                    status=change.status,
                    additions=change.additions,
                    deletions=change.deletions,
                    previous_path=change.previous_path,
                )
                for change in snapshot.files
            ],
            diff=snapshot.diff,
            diff_truncated=snapshot.diff_truncated,
            can_apply=state in {"pending", "reverted"} and blocked_reason is None,
            can_discard=state in {"pending", "reverted"} and terminal,
            can_undo=state == "applied",
            blocked_reason=blocked_reason,
        )

    async def apply_change_set(self, task_id: uuid.UUID) -> ChangeSetRead:
        task = await self.get(task_id)
        if not task.project_source_path:
            raise NotFoundError("This task is not bound to a local Git project.")
        lock = await asyncio.to_thread(acquire_source_lock, task.project_source_path)
        try:
            await self.tasks.session.refresh(task)
            return await self._apply_change_set_locked(task)
        finally:
            await asyncio.to_thread(release_source_lock, lock)

    async def _apply_change_set_locked(self, task: TaskModel) -> ChangeSetRead:
        if task.change_state not in {"pending", "reverted"}:
            state = task.change_state or "unavailable"
            raise ConflictError(f"Change set is {state} and cannot apply.")
        preview = await self.inspect_change_set(task.id)
        if not preview.can_apply:
            raise ConflictError(preview.blocked_reason or "This change set cannot be applied.")
        if not task.project_source_path or not task.project_base_commit or not task.workspace_path:
            raise NotFoundError("This task is not bound to a local Git project.")
        source_path = task.project_source_path
        base_commit = task.project_base_commit
        snapshot = await asyncio.to_thread(inspect_changes, Path(task.workspace_path), base_commit)
        await asyncio.to_thread(save_patch, task.id, snapshot.patch)
        try:
            await asyncio.to_thread(
                apply_patch,
                source_path,
                base_commit,
                snapshot.patch,
            )
        except Exception:
            await asyncio.to_thread(delete_patch, task.id)
            raise
        task.change_state = "applied"
        task.applied_patch_sha256 = snapshot.patch_sha256
        try:
            await self.tasks.session.commit()
            await self.tasks.session.refresh(task)
        except Exception:
            await self.tasks.session.rollback()
            await asyncio.to_thread(
                apply_patch,
                source_path,
                base_commit,
                snapshot.patch,
                reverse=True,
            )
            await asyncio.to_thread(delete_patch, task.id)
            raise
        return await self.inspect_change_set(task.id)

    async def discard_change_set(self, task_id: uuid.UUID) -> ChangeSetRead:
        task = await self.get(task_id)
        if not task.project_source_path:
            raise NotFoundError("This task is not bound to a local Git project.")
        lock = await asyncio.to_thread(acquire_source_lock, task.project_source_path)
        try:
            await self.tasks.session.refresh(task)
            return await self._discard_change_set_locked(task)
        finally:
            await asyncio.to_thread(release_source_lock, lock)

    async def _discard_change_set_locked(self, task: TaskModel) -> ChangeSetRead:
        preview = await self.inspect_change_set(task.id)
        if not preview.can_discard:
            raise ConflictError(f"Change set is {preview.state} and cannot be discarded.")
        task.change_state = "discarded"
        task.applied_patch_sha256 = None
        await self.tasks.session.commit()
        await self.tasks.session.refresh(task)
        await asyncio.to_thread(delete_patch, task.id)
        return await self.inspect_change_set(task.id)

    async def undo_change_set(self, task_id: uuid.UUID) -> ChangeSetRead:
        task = await self.get(task_id)
        if not task.project_source_path:
            raise NotFoundError("This task is not bound to a local Git project.")
        lock = await asyncio.to_thread(acquire_source_lock, task.project_source_path)
        try:
            await self.tasks.session.refresh(task)
            return await self._undo_change_set_locked(task)
        finally:
            await asyncio.to_thread(release_source_lock, lock)

    async def _undo_change_set_locked(self, task: TaskModel) -> ChangeSetRead:
        if task.change_state != "applied" or not task.applied_patch_sha256:
            state = task.change_state or "unavailable"
            raise ConflictError(f"Change set is {state} and cannot undo.")
        if not task.project_source_path or not task.project_base_commit:
            raise NotFoundError("This task is not bound to a local Git project.")
        source_path = task.project_source_path
        base_commit = task.project_base_commit
        patch = await asyncio.to_thread(load_patch, task.id, task.applied_patch_sha256)
        await asyncio.to_thread(
            apply_patch,
            source_path,
            base_commit,
            patch,
            reverse=True,
        )
        task.change_state = "reverted"
        try:
            await self.tasks.session.commit()
            await self.tasks.session.refresh(task)
        except Exception:
            await self.tasks.session.rollback()
            await asyncio.to_thread(
                apply_patch,
                source_path,
                base_commit,
                patch,
                allow_dirty=True,
            )
            raise
        return await self.inspect_change_set(task.id)

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

    async def replay_receipt(self, task_id: uuid.UUID) -> dict[str, Any]:
        from app.services.completion import attach_baseline, completion_gates_pass
        from app.services.verification import as_dicts, run_checks
        from app.tools.envelope import CapabilityEnvelope

        task = await self.get(task_id)
        receipt = await self.get_receipt(task_id)
        ws = await self._workspace(task_id)
        if receipt is None or ws is None:
            RECEIPT_REPLAYS.labels(outcome="missing").inc()
            raise NotFoundError("This task has no replayable Receipt.")
        from app.services.receipt import verify_receipt_full

        integrity = verify_receipt_full(receipt, workspace=ws, db_anchor=task.receipt_hash)
        if not integrity["valid"]:
            RECEIPT_REPLAYS.labels(outcome="integrity_refused").inc()
            raise ConflictError("Receipt integrity verification failed; replay was refused.")
        raw_checks = receipt.get("checks")
        definitions = (
            [
                check["definition"]
                for check in raw_checks
                if isinstance(check, dict) and isinstance(check.get("definition"), dict)
            ]
            if isinstance(raw_checks, list)
            else []
        )
        if not definitions:
            RECEIPT_REPLAYS.labels(outcome="not_replayable").inc()
            raise ConflictError("This Receipt has no replayable check definitions.")
        backend = (
            "kubernetes"
            if task.sandbox == "kubernetes"
            else ("docker" if task.sandbox == "container" else None)
        )
        if backend is None and settings.agent_sandbox not in {"off", "inline"}:
            RECEIPT_REPLAYS.labels(outcome="sandbox_refused").inc()
            raise ConflictError("Replay refuses the host because no sandbox is available.")
        results = attach_baseline(
            await run_checks(
                definitions,
                ws,
                envelope=CapabilityEnvelope.from_capabilities(
                    task.resolved_capabilities, egress_hosts=task.egress_hosts
                ),
                sandbox_image=settings.agent_sandbox_image if backend else None,
                sandbox_backend=backend,
                command_timeout=settings.agent_command_timeout_seconds,
                output_limit=settings.agent_command_output_limit,
                sandbox_memory=settings.agent_sandbox_memory,
                sandbox_cpus=settings.agent_sandbox_cpus,
                criterion_count=len(task.rubric or []),
                infer_criterion_ids=False,
            ),
            receipt.get("baseline_checks") or [],
        )
        passed = bool(results) and completion_gates_pass(results)
        RECEIPT_REPLAYS.labels(outcome="passed" if passed else "failed").inc()
        return {
            "passed": passed,
            "checks": as_dicts(results),
        }

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
                thought=last.thought,
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
