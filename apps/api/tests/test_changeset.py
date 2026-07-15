from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import settings
from app.db.models.task import TaskModel
from app.domain.task import StopReason, TaskStatus
from app.services.changeset import acquire_source_lock, release_source_lock
from app.services.receipt import RECEIPT_SCHEMA, build_receipt
from app.tools.workspace import Workspace


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path, name: str = "project") -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "loop@example.com")
    _git(repo, "config", "user.name", "Loop Test")
    (repo / "app.py").write_text("print('before')\n")
    (repo / "asset.bin").write_bytes(b"\x00before")
    (repo / "delete.txt").write_text("remove me\n")
    (repo / "rename-me.txt").write_text("rename me\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "initial")
    return repo


async def _mark_verified(engine: AsyncEngine, task_id: str) -> None:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        task = await session.get(TaskModel, uuid.UUID(task_id))
        assert task is not None and task.workspace_path
        task.status = TaskStatus.COMPLETED.value
        task.stop_reason = StopReason.GOAL_ACHIEVED.value
        task.verified_by = "execution"
        task.verification_score = 100
        task.summary = "Updated and checked the project."
        receipt_hash, _ = build_receipt(
            task,
            [],
            score=100,
            verified_by="execution",
            workspace=Workspace(Path(task.workspace_path)),
        )
        task.receipt_hash = receipt_hash
        task.receipt_schema = RECEIPT_SCHEMA
        await session.commit()


async def _workspace_path(engine: AsyncEngine, task_id: str) -> Path:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        task = await session.get(TaskModel, uuid.UUID(task_id))
        assert task is not None and task.workspace_path
        return Path(task.workspace_path)


async def test_verified_change_set_apply_undo_and_discard(
    client: AsyncClient,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source = _repository(projects)
    workspaces = tmp_path / "workspaces"
    monkeypatch.setattr(settings, "loop_local_projects_root", str(projects))
    monkeypatch.setattr(settings, "agent_workspaces_root", str(workspaces))

    created = await client.post(
        "/api/v1/tasks",
        json={
            "goal": "Update the project and verify it",
            "project_path": "project",
            "autostart": False,
            "idempotency_key": "verified-project-task",
        },
    )
    assert created.status_code == 201
    body = created.json()
    task_id = body["id"]
    assert body["change_set"]["project_path"] == "project"
    assert body["change_set"]["state"] == "pending"
    duplicate = await client.post(
        "/api/v1/tasks",
        json={
            "goal": "Update the project and verify it",
            "project_path": "project",
            "autostart": False,
            "idempotency_key": "verified-project-task",
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == task_id

    assert "workspace_path" not in body
    isolated = await _workspace_path(engine, task_id)
    assert isolated != source
    assert _git(isolated, "remote") == ""
    (isolated / "app.py").write_text("print('after')\n")
    (isolated / "asset.bin").write_bytes(b"\x00after")
    (isolated / "delete.txt").unlink()
    (isolated / "rename-me.txt").rename(isolated / "renamed.txt")
    (isolated / "new.txt").write_text("new output\n")
    (isolated / "receipt.json").write_text("agent cannot smuggle this into the patch")
    _git(isolated, "config", "user.email", "agent@example.com")
    _git(isolated, "config", "user.name", "Agent")
    _git(isolated, "add", "-A")
    _git(isolated, "commit", "--quiet", "-m", "agent commit")
    assert (source / "app.py").read_text() == "print('before')\n"

    await _mark_verified(engine, task_id)
    review = await client.get(f"/api/v1/tasks/{task_id}/changes")
    assert review.status_code == 200
    change_set = review.json()
    assert change_set["can_apply"] is True
    assert {item["path"] for item in change_set["files"]} == {
        "app.py",
        "asset.bin",
        "delete.txt",
        "new.txt",
        "renamed.txt",
    }
    rename = next(item for item in change_set["files"] if item["path"] == "renamed.txt")
    assert rename["status"].startswith("R")
    assert rename["previous_path"] == "rename-me.txt"
    binary = next(item for item in change_set["files"] if item["path"] == "asset.bin")
    assert binary["additions"] is None and binary["deletions"] is None
    assert "receipt.json" not in change_set["diff"]

    lock = acquire_source_lock(str(source))
    try:
        concurrent = await client.post(f"/api/v1/tasks/{task_id}/changes/apply")
        assert concurrent.status_code == 409
        assert "in progress" in concurrent.json()["detail"]
    finally:
        release_source_lock(lock)

    applied = await client.post(f"/api/v1/tasks/{task_id}/changes/apply")
    assert applied.status_code == 200
    assert applied.json()["state"] == "applied"
    assert (source / "app.py").read_text() == "print('after')\n"
    assert (source / "asset.bin").read_bytes() == b"\x00after"
    assert not (source / "delete.txt").exists()
    assert not (source / "rename-me.txt").exists()
    assert (source / "renamed.txt").read_text() == "rename me\n"
    assert (source / "new.txt").read_text() == "new output\n"
    assert not (source / "receipt.json").exists()

    second_apply = await client.post(f"/api/v1/tasks/{task_id}/changes/apply")
    assert second_apply.status_code == 409

    (source / "app.py").write_text("print('user edit')\n")
    conflicted_undo = await client.post(f"/api/v1/tasks/{task_id}/changes/undo")
    assert conflicted_undo.status_code == 409
    assert (source / "new.txt").exists()
    still_applied = await client.get(f"/api/v1/tasks/{task_id}/changes")
    assert still_applied.json()["state"] == "applied"
    (source / "app.py").write_text("print('after')\n")

    undone = await client.post(f"/api/v1/tasks/{task_id}/changes/undo")
    assert undone.status_code == 200
    assert undone.json()["state"] == "reverted"
    assert (source / "app.py").read_text() == "print('before')\n"
    assert (source / "asset.bin").read_bytes() == b"\x00before"
    assert (source / "delete.txt").read_text() == "remove me\n"
    assert (source / "rename-me.txt").read_text() == "rename me\n"
    assert not (source / "renamed.txt").exists()
    assert not (source / "new.txt").exists()
    assert _git(source, "status", "--porcelain") == ""

    discarded = await client.post(f"/api/v1/tasks/{task_id}/changes/discard")
    assert discarded.status_code == 200
    assert discarded.json()["state"] == "discarded"
    refused = await client.post(f"/api/v1/tasks/{task_id}/changes/apply")
    assert refused.status_code == 409


async def test_project_binding_refuses_dirty_and_escaping_sources(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    source = _repository(projects)
    monkeypatch.setattr(settings, "loop_local_projects_root", str(projects))
    monkeypatch.setattr(settings, "agent_workspaces_root", str(tmp_path / "workspaces"))

    (source / "app.py").write_text("dirty\n")
    dirty = await client.post(
        "/api/v1/tasks",
        json={"goal": "Change a dirty project", "project_path": "project", "autostart": False},
    )
    assert dirty.status_code == 409
    assert "uncommitted changes" in dirty.json()["detail"]

    escaping = await client.post(
        "/api/v1/tasks",
        json={"goal": "Escape the project root", "project_path": "../outside", "autostart": False},
    )
    assert escaping.status_code == 422


async def test_apply_refuses_a_diff_changed_after_receipt(
    client: AsyncClient,
    engine: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    _repository(projects)
    monkeypatch.setattr(settings, "loop_local_projects_root", str(projects))
    monkeypatch.setattr(settings, "agent_workspaces_root", str(tmp_path / "workspaces"))

    created = (
        await client.post(
            "/api/v1/tasks",
            json={"goal": "Make a verified edit", "project_path": "project", "autostart": False},
        )
    ).json()
    isolated = await _workspace_path(engine, created["id"])
    (isolated / "app.py").write_text("print('verified')\n")
    await _mark_verified(engine, created["id"])
    (isolated / "app.py").write_text("print('tampered')\n")

    response = await client.post(f"/api/v1/tasks/{created['id']}/changes/apply")
    assert response.status_code == 409
    assert "Receipt" in response.json()["detail"]
