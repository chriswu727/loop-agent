"""The agent loop must respect every limit. These tests drive each stop
condition deterministically with a scripted fake model — no network, no flakiness.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import LLMResult
from app.domain.task import StopReason, TaskStatus
from app.repositories.iteration import IterationRepository
from app.repositories.task import TaskRepository
from app.services.agent_loop import AgentLoopService


class ScriptedLLM:
    """Returns a rubric, then alternating produce/critique replies. Critique
    scores are read off a script so a test can dictate the loop's trajectory."""

    def __init__(self, scores: Sequence[int], *, produce_tokens: int = 100,
                 critique_tokens: int = 100, understand_tokens: int = 10) -> None:
        self._scores = list(scores)
        self._i = 0
        self.produce_tokens = produce_tokens
        self.critique_tokens = critique_tokens
        self.understand_tokens = understand_tokens
        self.produce_calls = 0

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 4096, temperature: float = 0.7
    ) -> LLMResult:
        if "JSON array" in user:  # understand phase
            return LLMResult('["criterion one", "criterion two"]', "fake", self.understand_tokens)
        if "JSON object" in user:  # critique phase
            score = self._scores[min(self._i, len(self._scores) - 1)]
            self._i += 1
            body = f'{{"score": {score}, "weaknesses": ["x"], "directives": ["improve"]}}'
            return LLMResult(body, "fake", self.critique_tokens)
        # produce phase
        self.produce_calls += 1
        return LLMResult(f"draft v{self.produce_calls}", "fake", self.produce_tokens)


async def _make_task(
    session: AsyncSession, *, max_iterations: int, token_budget: int, target_score: int
):
    repo = TaskRepository(session)
    task = await repo.create(
        goal="write something good",
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
    await session.commit()
    return task


def _service(session: AsyncSession, llm: ScriptedLLM) -> AgentLoopService:
    return AgentLoopService(TaskRepository(session), IterationRepository(session), llm)


async def test_stops_when_target_reached(session: AsyncSession) -> None:
    task = await _make_task(session, max_iterations=10, token_budget=1_000_000, target_score=90)
    await _service(session, ScriptedLLM([95])).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.stop_reason == StopReason.TARGET_REACHED.value
    assert task.best_score == 95
    assert task.iterations_used == 1
    assert task.best_artifact == "draft v1"


async def test_stops_at_iteration_cap(session: AsyncSession) -> None:
    # Ascending scores that never reach the (impossible) target -> cap decides.
    task = await _make_task(session, max_iterations=3, token_budget=1_000_000, target_score=100)
    await _service(session, ScriptedLLM([50, 65, 80])).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.MAX_ITERATIONS.value
    assert task.iterations_used == 3
    assert task.best_score == 80


async def test_stops_on_plateau(session: AsyncSession) -> None:
    # Flat scores: no real gain for `patience` passes -> plateau.
    task = await _make_task(session, max_iterations=10, token_budget=1_000_000, target_score=100)
    await _service(session, ScriptedLLM([50, 50, 50, 50])).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.PLATEAU.value
    assert task.iterations_used == 3  # pass1 set baseline, 2 flat passes hit patience=2


async def test_stops_when_budget_exhausted(session: AsyncSession) -> None:
    # One pass costs 10 (understand) + 100 + 100 = 210; budget 150 is gone after pass 1.
    task = await _make_task(session, max_iterations=10, token_budget=150, target_score=100)
    await _service(session, ScriptedLLM([40, 50, 60])).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.BUDGET_EXHAUSTED.value
    assert task.iterations_used == 1
    assert task.tokens_used >= 150


async def test_records_each_iteration_and_keeps_best(session: AsyncSession) -> None:
    task = await _make_task(session, max_iterations=3, token_budget=1_000_000, target_score=100)
    # A regression in the middle must not lower the recorded best.
    await _service(session, ScriptedLLM([60, 30, 75])).run(task.id)

    iterations = await IterationRepository(session).list_for_task(task.id)
    assert [it.number for it in iterations] == [1, 2, 3]
    assert [it.score for it in iterations] == [60, 30, 75]

    await session.refresh(task)
    assert task.best_score == 75
    assert task.best_artifact == "draft v3"
