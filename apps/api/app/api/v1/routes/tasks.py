"""HTTP surface for the agent loop.

Thin transport: validate, call the service, map to DTOs. The one bit of
orchestration here is scheduling the run *after* the request commits, via a
background task, so the loop never sees a half-written row.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse

from app.api.v1.deps import TaskServiceDep, rate_limit
from app.core.config import settings
from app.db.session import get_sessionmaker
from app.exceptions import NotFoundError
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.common import Page
from app.schemas.file import FileContent, FileEntry
from app.schemas.step import LedgerStatus, StepRead
from app.schemas.task import (
    ChangeSetRead,
    LimitDefaults,
    RespondIn,
    TaskCreate,
    TaskRead,
    TaskSnapshot,
)
from app.services.runner import trigger_task
from app.services.task import TaskService

_TERMINAL = {"completed", "cancelled", "failed"}

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
    root: bool = Query(default=True, description="Only top-level tasks (hide sub-agents)"),
) -> Page[TaskRead]:
    tasks, total = await service.list_tasks(limit=limit, offset=offset, root_only=root)
    return Page[TaskRead](
        items=[TaskRead.from_model(t) for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{task_id}/children",
    response_model=list[TaskRead],
    summary="Sub-agents spawned by this task",
)
async def task_children(task_id: uuid.UUID, service: TaskServiceDep) -> list[TaskRead]:
    return [TaskRead.from_model(t) for t in await service.list_children(task_id)]


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
    # autostart=false leaves the task a draft so files can be uploaded first;
    # the client then calls /start. Otherwise run after the response/commit.
    if payload.autostart:
        background.add_task(trigger_task, task.id)
    return TaskRead.from_model(task)


@router.post(
    "/{task_id}/files",
    response_model=list[FileEntry],
    summary="Upload a file into the task workspace (before it runs)",
)
async def upload_file(
    task_id: uuid.UUID, service: TaskServiceDep, file: UploadFile = File(...)
) -> list[FileEntry]:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1_048_576):
        total += len(chunk)
        if total > settings.agent_max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Upload exceeds the {settings.agent_max_upload_bytes}-byte limit.",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    await service.save_upload(task_id, file.filename or "upload.bin", data)
    return [FileEntry(path=p, size=s) for p, s in await service.list_files(task_id)]


@router.post("/{task_id}/start", response_model=TaskRead, summary="Start a draft task")
async def start_task(
    task_id: uuid.UUID, service: TaskServiceDep, background: BackgroundTasks
) -> TaskRead:
    task = await service.start(task_id)
    background.add_task(trigger_task, task.id)
    return TaskRead.from_model(task)


@router.post(
    "/{task_id}/retry",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Re-run a finished task as a fresh task with the same settings",
)
async def retry_task(
    task_id: uuid.UUID, service: TaskServiceDep, background: BackgroundTasks
) -> TaskRead:
    task = await service.retry(task_id)
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


@router.get(
    "/{task_id}/ledger",
    response_model=LedgerStatus,
    summary="Re-verify the task's tamper-evident step chain",
)
async def verify_ledger(task_id: uuid.UUID, service: TaskServiceDep) -> LedgerStatus:
    return LedgerStatus.model_validate(await service.verify_ledger(task_id))


@router.get("/{task_id}/receipt", summary="The task's Receipt + layered re-verification")
async def task_receipt(task_id: uuid.UUID, service: TaskServiceDep) -> dict[str, Any]:
    report = await service.get_receipt_report(task_id)
    if report is None:
        raise NotFoundError("This task has no Receipt yet (it hasn't reached a terminal state).")
    return report


@router.post("/{task_id}/receipt/replay", summary="Re-run a Receipt's recorded checks")
async def replay_task_receipt(task_id: uuid.UUID, service: TaskServiceDep) -> dict[str, Any]:
    return await service.replay_receipt(task_id)


@router.get(
    "/{task_id}/changes",
    response_model=ChangeSetRead,
    summary="Review an isolated local-project change set",
)
async def task_changes(task_id: uuid.UUID, service: TaskServiceDep) -> ChangeSetRead:
    return await service.inspect_change_set(task_id)


@router.post(
    "/{task_id}/changes/apply",
    response_model=ChangeSetRead,
    summary="Apply the exact execution-verified patch to its source project",
)
async def apply_task_changes(task_id: uuid.UUID, service: TaskServiceDep) -> ChangeSetRead:
    return await service.apply_change_set(task_id)


@router.post(
    "/{task_id}/changes/discard",
    response_model=ChangeSetRead,
    summary="Permanently reject a task's change set",
)
async def discard_task_changes(task_id: uuid.UUID, service: TaskServiceDep) -> ChangeSetRead:
    return await service.discard_change_set(task_id)


@router.post(
    "/{task_id}/changes/undo",
    response_model=ChangeSetRead,
    summary="Reverse the exact patch previously applied by Loop",
)
async def undo_task_changes(task_id: uuid.UUID, service: TaskServiceDep) -> ChangeSetRead:
    return await service.undo_change_set(task_id)


async def _build_snapshot(service: TaskService, task_id: uuid.UUID) -> TaskSnapshot:
    task = await service.get(task_id)
    steps = await service.list_steps(task_id)
    files = await service.list_files(task_id)
    ledger = await service.verify_ledger(task_id, steps=steps)  # reuse the loaded steps
    return TaskSnapshot(
        task=TaskRead.from_model(task),
        steps=[StepRead.model_validate(s) for s in steps],
        files=[FileEntry(path=p, size=s) for p, s in files],
        ledger=LedgerStatus.model_validate(ledger),
    )


async def _event_stream(task_id: uuid.UUID, subject: str) -> AsyncIterator[str]:
    """Push a full task snapshot whenever something changes, until terminal. Each
    tick does a *cheap* fingerprint fetch (one task row) and only rebuilds the full
    snapshot — steps, files, ledger re-hash — when it actually changed, so an idle
    open tab costs one small query per tick instead of dozens."""
    sessionmaker = get_sessionmaker()
    last_fp: tuple[object, ...] | None = None
    for _ in range(1800):  # ~15 min safety cap at 0.5s cadence
        async with sessionmaker() as session:
            service = TaskService(TaskRepository(session), StepRepository(session), subject=subject)
            try:
                task = await service.get(task_id)
            except NotFoundError:
                yield 'event: error\ndata: {"detail":"task not found"}\n\n'
                return
            fp = (task.status, task.steps_used, task.tokens_used, task.updated_at.isoformat())
            terminal = task.status in _TERMINAL
            if fp != last_fp:
                last_fp = fp
                snapshot = await _build_snapshot(service, task_id)
                yield f"data: {snapshot.model_dump_json()}\n\n"
        if terminal:
            return
        await asyncio.sleep(0.5)


@router.get("/{task_id}/events", summary="Stream live task updates (SSE)")
async def task_events(task_id: uuid.UUID, request: Request) -> StreamingResponse:
    subject = str(getattr(request.state, "subject", "local"))
    return StreamingResponse(
        _event_stream(task_id, subject),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{task_id}/files", response_model=list[FileEntry], summary="List the task's output files"
)
async def list_files(task_id: uuid.UUID, service: TaskServiceDep) -> list[FileEntry]:
    files = await service.list_files(task_id)
    return [FileEntry(path=p, size=s) for p, s in files]


@router.get(
    "/{task_id}/files/{path:path}",
    response_model=FileContent,
    summary="View one output file's content",
)
async def view_file(task_id: uuid.UUID, path: str, service: TaskServiceDep) -> FileContent:
    content, size, truncated = await service.read_file(task_id, path)
    return FileContent(path=path, content=content, size=size, truncated=truncated)


@router.get(
    "/{task_id}/download/{path:path}",
    summary="Download one output file",
    response_class=FileResponse,
)
async def download_file(task_id: uuid.UUID, path: str, service: TaskServiceDep) -> FileResponse:
    target = await service.resolve_file(task_id, path)
    return FileResponse(target, filename=target.name)


@router.post("/{task_id}/cancel", response_model=TaskRead, summary="Cancel a running task")
async def cancel_task(task_id: uuid.UUID, service: TaskServiceDep) -> TaskRead:
    task = await service.cancel(task_id)
    return TaskRead.from_model(task)


@router.post(
    "/{task_id}/respond",
    response_model=TaskRead,
    summary="Answer the agent's question and resume the run",
)
async def respond_to_task(
    task_id: uuid.UUID, payload: RespondIn, service: TaskServiceDep, background: BackgroundTasks
) -> TaskRead:
    task = await service.respond(task_id, payload.answer)
    background.add_task(trigger_task, task.id)  # resume after the commit
    return TaskRead.from_model(task)
