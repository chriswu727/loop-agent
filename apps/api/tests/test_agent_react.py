"""The agent must respect every limit and stop cleanly. These drive the loop with
a scripted fake model — no network — so each stop condition is proven.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import LLMResult
from app.domain.task import StopReason, TaskStatus
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.agent_react import AgentReactService


class ScriptedLLM:
    """Returns a rubric for the understand call, a scripted decision for each plan
    call, and a fixed verdict for the verify call."""

    def __init__(
        self,
        plans: list[dict[str, Any]],
        *,
        verify: dict[str, Any] | None = None,
        plan_tokens: int = 100,
        understand_tokens: int = 10,
        verify_tokens: int = 20,
    ) -> None:
        self._plans = plans
        self._i = 0
        self._verify = verify or {"score": 90, "met": True, "missing": []}
        self.plan_tokens = plan_tokens
        self.understand_tokens = understand_tokens
        self.verify_tokens = verify_tokens

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 4096, temperature: float = 0.7
    ) -> LLMResult:
        if "JSON array of 3 to 6" in user:  # understand
            return LLMResult('["produce a correct result"]', "fake", self.understand_tokens)
        if '"met"' in user:  # verify
            return LLMResult(json.dumps(self._verify), "fake", self.verify_tokens)
        decision = self._plans[min(self._i, len(self._plans) - 1)]  # plan
        self._i += 1
        return LLMResult(json.dumps(decision), "fake", self.plan_tokens)


async def _make_task(session: AsyncSession, *, max_steps: int, token_budget: int):
    repo = TaskRepository(session)
    task = await repo.create(
        goal="do the thing",
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
    await session.commit()
    return task


def _service(session: AsyncSession, llm: ScriptedLLM) -> AgentReactService:
    return AgentReactService(TaskRepository(session), StepRepository(session), llm)


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_workspaces_root", str(tmp_path / "ws"))


async def test_goal_achieved_when_verifier_accepts_finish(session: AsyncSession) -> None:
    plans = [
        {"thought": "write the file", "tool": "write_file",
         "args": {"path": "result.txt", "content": "done"}},
        {"thought": "all set", "tool": "finish", "args": {"summary": "wrote result.txt"}},
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 92, "met": True, "missing": []})
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.verification_score == 92
    assert task.summary == "wrote result.txt"
    # The file the agent "wrote" really exists in its workspace.
    assert (Path(task.workspace_path) / "result.txt").read_text() == "done"


async def test_rejected_finish_then_gives_up_stuck(session: AsyncSession) -> None:
    # Agent keeps declaring done; verifier keeps rejecting -> stuck after retries.
    plans = [{"thought": "done?", "tool": "finish", "args": {"summary": "maybe"}}]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 30, "met": False, "missing": ["nothing produced"]})
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.STUCK.value
    assert task.status == TaskStatus.COMPLETED.value


async def test_stops_at_step_cap(session: AsyncSession) -> None:
    plans = [{"thought": "keep writing", "tool": "write_file",
              "args": {"path": "a.txt", "content": "x"}}]
    task = await _make_task(session, max_steps=3, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans)).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.MAX_STEPS.value
    assert task.steps_used == 3


async def test_stops_when_budget_exhausted(session: AsyncSession) -> None:
    plans = [{"thought": "write", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}}]
    # understand=10, first plan=100 -> 110 > 50 budget after step 1.
    task = await _make_task(session, max_steps=10, token_budget=50)
    await _service(session, ScriptedLLM(plans, understand_tokens=10, plan_tokens=100)).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.BUDGET_EXHAUSTED.value
    assert task.tokens_used >= 50


async def test_rewriting_same_file_without_running_is_stuck(session: AsyncSession) -> None:
    # write_file always returns "ok", but rewriting one file forever is no progress.
    plans = [{"thought": "again", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}}]
    task = await _make_task(session, max_steps=20, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans)).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.STUCK.value
    assert task.steps_used <= settings.agent_stuck_threshold + 1


async def test_stops_when_stuck_on_repeated_failures(session: AsyncSession) -> None:
    # An invalid tool name fails every step; after the stuck threshold the loop quits.
    plans = [{"thought": "??", "tool": "frobnicate", "args": {}}]
    task = await _make_task(session, max_steps=20, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans)).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.STUCK.value
    assert task.steps_used == settings.agent_stuck_threshold


async def test_ask_user_pauses_then_resumes_to_completion(session: AsyncSession) -> None:
    from app.repositories.step import StepRepository as _Steps
    from app.services.task import TaskService

    plans = [
        {"thought": "need input", "tool": "ask_user",
         "args": {"question": "Which language?"}},
        {"thought": "now build", "tool": "write_file",
         "args": {"path": "out.txt", "content": "python"}},
        {"thought": "done", "tool": "finish", "args": {"summary": "built it"}},
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 90, "met": True, "missing": []})
    service = _service(session, llm)

    # First run: the agent asks and pauses.
    await service.run(task.id)
    await session.refresh(task)
    assert task.status == TaskStatus.AWAITING_INPUT.value
    assert task.pending_question == "Which language?"
    assert task.steps_used == 1

    # The user answers; the task becomes resumable.
    tasks_service = TaskService(TaskRepository(session), _Steps(session))
    await tasks_service.respond(task.id, "Python")
    await session.refresh(task)
    assert task.status == TaskStatus.PENDING.value
    assert task.pending_question is None

    # Second run resumes from history and finishes.
    await service.run(task.id)
    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.summary == "built it"


async def test_run_produces_a_verifiable_hash_chain(session: AsyncSession) -> None:
    from app.services.ledger import verify_chain

    plans = [
        {"thought": "w", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {"thought": "done", "tool": "finish", "args": {"summary": "done"}},
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans, verify={"score": 90, "met": True})).run(task.id)

    steps = await StepRepository(session).list_for_task(task.id)
    assert len(steps) >= 2
    ok, broken = verify_chain(task.id, steps)
    assert ok is True and broken is None
    # Tampering with a persisted step is detectable.
    steps[0].observation = "tampered"
    ok2, broken2 = verify_chain(task.id, steps)
    assert ok2 is False and broken2 == steps[0].number


async def test_passing_checks_yield_execution_verified_receipt(session: AsyncSession) -> None:
    plans = [
        {"thought": "write it", "tool": "write_file",
         "args": {"path": "out.txt", "content": "ready"}},
        {"thought": "prove it", "tool": "finish", "args": {
            "summary": "wrote out.txt",
            "checks": [
                {"kind": "file_exists", "path": "out.txt"},
                {"kind": "file_contains", "path": "out.txt", "text": "ready"},
            ],
        }},
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 95, "met": True, "missing": []})
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.verified_by == "execution"
    assert task.receipt_hash and len(task.receipt_hash) == 64
    # The Receipt is written into the workspace and re-readable.
    receipt = (Path(task.workspace_path) / "receipt.json").read_text()
    assert task.receipt_hash in receipt


async def test_failing_check_blocks_acceptance(session: AsyncSession) -> None:
    # The agent claims done with a check that cannot pass; the verifier refuses.
    plans = [{"thought": "claim", "tool": "finish", "args": {
        "summary": "all good",
        "checks": [{"kind": "file_exists", "path": "does-not-exist.txt"}],
    }}]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 99, "met": True, "missing": []})
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    # Even though the LLM said met=true, the failed check kept it from finishing.
    assert task.stop_reason == StopReason.STUCK.value
    assert task.verified_by is None
    assert task.receipt_hash is None


async def test_dangerous_command_is_blocked_not_run(session: AsyncSession) -> None:
    plans = [
        {"thought": "nuke it", "tool": "run_command", "args": {"command": "rm -rf /"}},
        {"thought": "give up", "tool": "finish", "args": {"summary": "blocked"}},
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 80, "met": True, "missing": []})
    await _service(session, llm).run(task.id)

    steps = await StepRepository(session).list_for_task(task.id)
    first = steps[0]
    assert first.tool == "run_command"
    assert first.status == "blocked"
    assert "policy" in first.observation.lower()
