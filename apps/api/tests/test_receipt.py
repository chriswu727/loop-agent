"""The Receipt is independently re-verifiable: recompute its content hash and any
tampering with a recorded fact is detected."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm.client import FallbackLLMClient
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.agent_react import AgentReactService
from app.services.receipt import _canonical_hash, verify_receipt
from app.services.task import TaskService


def test_verify_receipt_detects_tampering() -> None:
    body = {"goal": "x", "score": 90, "checks": [{"passed": True}]}
    receipt = {"receipt_hash": _canonical_hash(body), **body}
    assert verify_receipt(receipt)[0] is True
    receipt["score"] = 100  # tamper with a recorded fact
    assert verify_receipt(receipt)[0] is False


async def test_receipt_roundtrip_produces_and_verifies(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "agent_sandbox", "inline")
    monkeypatch.setattr(settings, "agent_workspaces_root", str(tmp_path / "ws"))
    monkeypatch.setattr(settings, "agent_memory_root", str(tmp_path / "mem"))

    repo = TaskRepository(session)
    task = await repo.create(
        goal="demo",
        status="pending",
        rubric=[],
        max_steps=8,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()
    await AgentReactService(repo, StepRepository(session), FallbackLLMClient(primary="mock")).run(
        task.id
    )

    service = TaskService(repo, StepRepository(session))
    receipt = await service.get_receipt(task.id)
    assert receipt is not None
    assert verify_receipt(receipt)[0] is True  # the real produced Receipt verifies
    receipt["goal"] = "not what actually ran"
    assert verify_receipt(receipt)[0] is False


async def test_receipt_endpoint_404_without_receipt(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "no receipt yet"})).json()
    resp = await client.get(f"/api/v1/tasks/{created['id']}/receipt")
    assert resp.status_code == 404
