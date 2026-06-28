"""The agent loop engine — the heart of the product.

Given a published task it runs: understand the goal into a rubric, then loop
produce -> critique -> reflect, carrying the best draft and the last critique
forward so each pass improves on the last. The loop stops the instant any hard
limit is hit, so a single task can never run away:

  * the critic's score reaches the target,
  * the iteration cap is reached,
  * the token budget is exhausted,
  * progress plateaus (no real score gain for N passes), or
  * the user cancels.

The engine talks only to the :class:`LLMClient` protocol and the repositories,
so the full loop runs deterministically under test with a fake model.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.db.models.iteration import IterationModel
from app.db.models.task import TaskModel
from app.domain.task import StopReason, TaskStatus
from app.repositories.iteration import IterationRepository
from app.repositories.task import TaskRepository
from app.services.prompts import critique_prompts, produce_prompts, understand_prompts

log = get_logger("agent_loop")

# Optional hook fired after each persisted iteration (used by the worker to
# publish live events; a no-op under test).
IterationHook = Callable[[IterationModel], Awaitable[None]]


def _extract_json(text: str) -> object | None:
    """Best-effort: pull the first JSON object/array out of a model reply.

    Models occasionally wrap JSON in prose or code fences despite instructions,
    so we strip fences and grab the first balanced ``{...}`` or ``[...]``.
    """
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _clamp_score(value: object) -> int:
    try:
        score = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


class AgentLoopService:
    def __init__(
        self,
        tasks: TaskRepository,
        iterations: IterationRepository,
        llm: LLMClient,
        *,
        on_iteration: IterationHook | None = None,
    ) -> None:
        self.tasks = tasks
        self.iterations = iterations
        self.llm = llm
        self.on_iteration = on_iteration
        self.session = tasks.session  # shared unit of work

    async def run(self, task_id: uuid.UUID) -> None:
        task = await self.tasks.get(task_id)
        if task is None:
            log.warning("loop.task_missing", task_id=str(task_id))
            return
        if task.status != TaskStatus.PENDING.value:
            log.info("loop.skip_non_pending", task_id=str(task_id), status=task.status)
            return

        task.status = TaskStatus.RUNNING.value
        await self._commit()
        log.info("loop.start", task_id=str(task_id), goal=task.goal[:80])

        try:
            await self._run_loop(task)
        except Exception as exc:  # any unhandled error fails the task cleanly
            log.exception("loop.failed", task_id=str(task_id))
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = str(exc)[:1000]
            await self._commit()

    async def _run_loop(self, task: TaskModel) -> None:
        # --- Phase 0: understand the goal into a gradable rubric. ---
        rubric, tokens = await self._understand(task.goal)
        task.rubric = rubric
        task.tokens_used += tokens
        await self._commit()

        last_critique: str | None = None
        no_progress_streak = 0

        for number in range(1, task.max_iterations + 1):
            # Respect an external cancel issued between passes.
            await self.session.refresh(task)
            if task.status == TaskStatus.CANCELLED.value:
                task.stop_reason = StopReason.CANCELLED.value
                await self._commit()
                log.info("loop.cancelled", task_id=str(task.id))
                return

            # Budget gate before spending more on a pass.
            if task.tokens_used >= task.token_budget:
                await self._finish(task, StopReason.BUDGET_EXHAUSTED)
                return

            best_before = task.best_score

            # --- Produce, then critique. ---
            artifact, produce_tokens = await self._produce(
                task.goal, task.rubric, task.best_artifact, last_critique
            )
            score, critique_text, critique_tokens = await self._critique(
                task.goal, task.rubric, artifact
            )
            pass_tokens = produce_tokens + critique_tokens

            iteration = await self.iterations.create(
                task_id=task.id,
                number=number,
                artifact=artifact,
                score=score,
                critique=critique_text,
                tokens=pass_tokens,
            )

            task.iterations_used = number
            task.tokens_used += pass_tokens
            if score > task.best_score:
                task.best_score = score
                task.best_artifact = artifact
            await self._commit()

            if self.on_iteration is not None:
                await self.on_iteration(iteration)

            last_critique = critique_text
            log.info(
                "loop.iteration",
                task_id=str(task.id),
                number=number,
                score=score,
                tokens_used=task.tokens_used,
            )

            # --- Stop conditions, in priority order. ---
            if task.best_score >= task.target_score:
                await self._finish(task, StopReason.TARGET_REACHED)
                return
            if number >= task.max_iterations:
                await self._finish(task, StopReason.MAX_ITERATIONS)
                return
            if task.tokens_used >= task.token_budget:
                await self._finish(task, StopReason.BUDGET_EXHAUSTED)
                return

            if task.best_score - best_before < settings.loop_min_gain:
                no_progress_streak += 1
            else:
                no_progress_streak = 0
            if no_progress_streak >= settings.loop_plateau_patience:
                await self._finish(task, StopReason.PLATEAU)
                return

    # --- LLM phases -------------------------------------------------------

    async def _understand(self, goal: str) -> tuple[list[str], int]:
        system, user = understand_prompts(goal)
        result = await self.llm.complete(system, user, max_tokens=600, temperature=0.4)
        parsed = _extract_json(result.content)
        if isinstance(parsed, list):
            rubric = [str(c).strip() for c in parsed if str(c).strip()][:7]
        else:
            # Fallback: treat non-empty lines as criteria.
            rubric = [ln.strip("-* ").strip() for ln in result.content.splitlines() if ln.strip()][
                :7
            ]
        if not rubric:
            rubric = ["Fully and directly satisfies the task"]
        return rubric, result.tokens

    async def _produce(
        self,
        goal: str,
        rubric: list[str],
        best_artifact: str | None,
        last_critique: str | None,
    ) -> tuple[str, int]:
        system, user = produce_prompts(goal, rubric, best_artifact, last_critique)
        result = await self.llm.complete(system, user, max_tokens=2500, temperature=0.85)
        return result.content.strip(), result.tokens

    async def _critique(
        self, goal: str, rubric: list[str], artifact: str
    ) -> tuple[int, str, int]:
        system, user = critique_prompts(goal, rubric, artifact)
        result = await self.llm.complete(system, user, max_tokens=900, temperature=0.3)
        parsed = _extract_json(result.content)
        if isinstance(parsed, dict):
            score = _clamp_score(parsed.get("score"))
            weaknesses = parsed.get("weaknesses") or []
            directives = parsed.get("directives") or []
            critique_text = _format_critique(weaknesses, directives)
        else:
            score = _clamp_score(_first_number(result.content))
            critique_text = result.content.strip()[:2000]
        return score, critique_text, result.tokens

    # --- Persistence helpers ---------------------------------------------

    async def _finish(self, task: TaskModel, reason: StopReason) -> None:
        task.status = TaskStatus.COMPLETED.value
        task.stop_reason = reason.value
        await self._commit()
        log.info(
            "loop.finish",
            task_id=str(task.id),
            reason=reason.value,
            best_score=task.best_score,
            iterations=task.iterations_used,
            tokens=task.tokens_used,
        )

    async def _commit(self) -> None:
        await self.session.commit()


def _format_critique(weaknesses: object, directives: object) -> str:
    lines: list[str] = []
    if isinstance(weaknesses, list) and weaknesses:
        lines.append("Weaknesses:")
        lines += [f"- {str(w).strip()}" for w in weaknesses]
    if isinstance(directives, list) and directives:
        lines.append("Fix next:")
        lines += [f"- {str(d).strip()}" for d in directives]
    return "\n".join(lines)[:2000] or "No specific feedback provided."


def _first_number(text: str) -> str:
    match = re.search(r"\d{1,3}", text)
    return match.group(0) if match else "0"
