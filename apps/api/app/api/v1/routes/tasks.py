"""HTTP surface for the agent loop.

Thin transport: validate, call the service, map to DTOs. The one bit of
orchestration here is scheduling the run *after* the request commits, via a
background task, so the loop never sees a half-written row.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.v1.deps import TaskServiceDep, rate_limit
from app.schemas.common import Page
from app.schemas.file import FileContent, FileEntry
from app.schemas.step import StepRead
from app.schemas.task import LimitDefaults, RespondIn, TaskCreate, TaskRead
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
    data = await file.read()
    await service.save_upload(task_id, file.filename or "upload.bin", data)
    return [FileEntry(path=p, size=s) for p, s in await service.list_files(task_id)]


@router.post("/{task_id}/start", response_model=TaskRead, summary="Start a draft task")
async def start_task(
    task_id: uuid.UUID, service: TaskServiceDep, background: BackgroundTasks
) -> TaskRead:
    task = await service.start(task_id)
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
