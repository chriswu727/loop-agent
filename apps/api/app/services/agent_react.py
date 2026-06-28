"""The autonomous agent engine — the heart of the product.

Given a published task it runs a think → act → observe loop: understand the goal
into a rubric, then repeatedly plan a single action, execute a tool, and observe
the result, carrying the history forward, until the goal is verifiably done or a
hard limit stops it. It can read and write files and run shell commands inside a
sandboxed workspace.

Every limit is enforced so a task can never run away:
  * the verifier accepts the agent's "finish" (goal achieved),
  * the step cap is reached,
  * the token budget is exhausted,
  * it gets stuck (too many failed/blocked actions in a row), or
  * the user cancels.

The engine depends only on the LLM protocol, the repositories, and the tool
executor, so the whole loop runs deterministically under test with a fake model.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.db.models.task import TaskModel
from app.domain.task import StopReason, TaskStatus
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.prompts import plan_prompts, understand_prompts, verify_prompts
from app.tools import VALID_TOOLS, ToolExecutor, ToolStatus, Workspace

log = get_logger("agent")

# How many recent steps the planner sees in full; older steps collapse to a count.
_HISTORY_WINDOW = 12


def _extract_json(text: str) -> Any:
    """Best-effort: pull the first JSON object/array out of a model reply."""
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
        return max(0, min(100, int(float(value))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class AgentReactService:
    def __init__(
        self, tasks: TaskRepository, steps: StepRepository, llm: LLMClient
    ) -> None:
        self.tasks = tasks
        self.steps = steps
        self.llm = llm
        self.session = tasks.session
        self._history: list[str] = []

    async def run(self, task_id: uuid.UUID) -> None:
        """Run, or resume, a task. A task is resumable when it was paused on an
        ask_user question and the user has since answered (status back to
        pending with steps already on record)."""
        task = await self.tasks.get(task_id)
        if task is None:
            log.warning("agent.task_missing", task_id=str(task_id))
            return
        if task.status != TaskStatus.PENDING.value:
            log.info("agent.skip_non_pending", task_id=str(task_id), status=task.status)
            return

        workspace = Workspace(Path(task.workspace_path or settings.agent_workspaces_root) /
                              ("" if task.workspace_path else str(task.id)))
        executor = ToolExecutor(
            workspace,
            approval_mode=settings.agent_approval_mode,
            command_timeout=settings.agent_command_timeout_seconds,
            output_limit=settings.agent_command_output_limit,
        )
        task.status = TaskStatus.RUNNING.value
        task.workspace_path = str(workspace.root)
        # Rebuild the working memory from whatever has already happened so a
        # resumed run sees its own past actions (and the user's answer).
        await self._rebuild_history(task.id)
        await self._commit()
        resuming = task.steps_used > 0
        log.info("agent.start", task_id=str(task.id), resuming=resuming, goal=task.goal[:80])

        try:
            await self._run_loop(task, workspace, executor, start=task.steps_used + 1)
        except Exception as exc:  # any unhandled error fails the task cleanly
            log.exception("agent.failed", task_id=str(task.id))
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = str(exc)[:1000]
            await self._commit()

    async def _run_loop(
        self, task: TaskModel, workspace: Workspace, executor: ToolExecutor, *, start: int
    ) -> None:
        if not task.rubric:  # only on a fresh run, not a resume
            rubric, tokens = await self._understand(task.goal)
            task.rubric = rubric
            task.tokens_used += tokens
            await self._commit()

        consecutive_failures = 0
        finish_retries = 0

        for number in range(start, task.max_steps + 1):
            await self.session.refresh(task)
            if task.status == TaskStatus.CANCELLED.value:
                task.stop_reason = StopReason.CANCELLED.value
                await self._commit()
                return
            if task.tokens_used >= task.token_budget:
                await self._finish(task, StopReason.BUDGET_EXHAUSTED)
                return

            tokens_left = max(0, task.token_budget - task.tokens_used)
            system, user = plan_prompts(
                task.goal, task.rubric, workspace.tree(), self._history_view(),
                task.max_steps - number + 1, tokens_left,
            )
            result = await self.llm.complete(system, user, max_tokens=1200, temperature=0.5)
            step_tokens = result.tokens
            thought, tool, args = self._parse_decision(_extract_json(result.content))

            if tool == "finish":
                accepted, score, summary, _ = await self._handle_finish(
                    task, workspace, args, thought, number, step_tokens
                )
                if accepted:
                    return
                finish_retries += 1
                if finish_retries > settings.agent_max_finish_retries:
                    task.summary = summary
                    task.verification_score = score
                    await self._finish(task, StopReason.STUCK)
                    return
                continue

            if tool == "ask_user":
                await self._pause_for_user(task, args, thought, number, step_tokens)
                return  # the run resumes when the user answers

            if tool is None:
                observation, status = (
                    "Could not parse a valid action. Respond with one JSON object "
                    f"using a valid tool: {sorted(VALID_TOOLS)}.",
                    ToolStatus.ERROR,
                )
            else:
                tool_result = await executor.execute(tool, args)
                observation, status = tool_result.observation, tool_result.status

            await self._record_step(task, number, thought, tool or "invalid", args,
                                    observation, status, step_tokens)

            consecutive_failures = 0 if status is ToolStatus.OK else consecutive_failures + 1

            if number >= task.max_steps:
                await self._finish(task, StopReason.MAX_STEPS)
                return
            if task.tokens_used >= task.token_budget:
                await self._finish(task, StopReason.BUDGET_EXHAUSTED)
                return
            if consecutive_failures >= settings.agent_stuck_threshold:
                await self._finish(task, StopReason.STUCK)
                return

    async def _pause_for_user(
        self, task: TaskModel, args: dict[str, Any], thought: str, number: int, tokens: int
    ) -> None:
        question = str(args.get("question", "")).strip() or "(the agent asked a question)"
        await self._record_step(
            task, number, thought, "ask_user", {"question": question},
            "Waiting for the user's answer.", ToolStatus.OK, tokens,
        )
        task.pending_question = question
        task.status = TaskStatus.AWAITING_INPUT.value
        await self._commit()
        log.info("agent.awaiting_input", task_id=str(task.id), number=number)

    # --- LLM phases -------------------------------------------------------

    async def _understand(self, goal: str) -> tuple[list[str], int]:
        system, user = understand_prompts(goal)
        result = await self.llm.complete(system, user, max_tokens=500, temperature=0.4)
        parsed = _extract_json(result.content)
        if isinstance(parsed, list):
            rubric = [str(c).strip() for c in parsed if str(c).strip()][:6]
        else:
            rubric = [ln.strip("-* ").strip() for ln in result.content.splitlines() if ln.strip()][
                :6
            ]
        return (rubric or ["Fully and correctly satisfies the task"]), result.tokens

    async def _handle_finish(
        self,
        task: TaskModel,
        workspace: Workspace,
        args: dict[str, Any],
        thought: str,
        number: int,
        plan_tokens: int,
    ) -> tuple[bool, int, str, int]:
        """Verify a finish attempt. Returns (accepted, score, summary, verify_tokens)."""
        summary = str(args.get("summary", "")).strip() or "(no summary provided)"
        system, user = verify_prompts(task.goal, task.rubric, summary, workspace.tree())
        result = await self.llm.complete(system, user, max_tokens=500, temperature=0.2)
        parsed = _extract_json(result.content)
        if isinstance(parsed, dict):
            score = _clamp_score(parsed.get("score"))
            missing = parsed.get("missing") or []
            met = bool(parsed.get("met")) and score >= settings.agent_acceptance_score
        else:
            score, missing, met = 0, ["verifier returned no verdict"], False

        verdict = f"verifier: score {score}, met={met}"
        if missing:
            verdict += "\nmissing:\n" + "\n".join(f"- {m}" for m in missing)
        await self._record_step(task, number, thought, "finish", args, verdict,
                                ToolStatus.OK, plan_tokens + result.tokens)

        if met:
            task.summary = summary
            task.verification_score = score
            await self._finish(task, StopReason.GOAL_ACHIEVED)
            return True, score, summary, result.tokens
        return False, score, summary, result.tokens

    # --- Persistence helpers ---------------------------------------------

    def _parse_decision(self, decision: Any) -> tuple[str, str | None, dict[str, Any]]:
        if not isinstance(decision, dict):
            return "", None, {}
        thought = str(decision.get("thought", "")).strip()
        tool = decision.get("tool")
        args = decision.get("args")
        if not isinstance(args, dict):
            args = {}
        if tool not in VALID_TOOLS:
            return thought, None, {}
        return thought, str(tool), args

    async def _record_step(
        self,
        task: TaskModel,
        number: int,
        thought: str,
        tool: str,
        args: dict[str, Any],
        observation: str,
        status: ToolStatus,
        tokens: int,
    ) -> None:
        await self.steps.create(
            task_id=task.id,
            number=number,
            thought=thought,
            tool=tool,
            tool_args=args,
            observation=observation,
            status=status.value,
            tokens=tokens,
        )
        task.steps_used = number
        task.tokens_used += tokens
        await self._commit()
        self._history.append(self._format_history(number, thought, tool, args, observation, status))
        log.info("agent.step", task_id=str(task.id), number=number, tool=tool, status=status.value)

    @staticmethod
    def _format_history(
        number: int, thought: str, tool: str, args: dict[str, Any],
        observation: str, status: ToolStatus,
    ) -> str:
        arg_preview = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
        obs = observation if len(observation) <= 600 else observation[:600] + " …[truncated]"
        return (
            f"Step {number} [{tool}] ({status.value}): {thought}\n"
            f"  args: {arg_preview}\n  -> {obs}"
        )

    async def _rebuild_history(self, task_id: uuid.UUID) -> None:
        """Reconstruct working memory from persisted steps (used on resume)."""
        self._history = [
            self._format_history(
                s.number, s.thought, s.tool, s.tool_args, s.observation, ToolStatus(s.status)
            )
            for s in await self.steps.list_for_task(task_id)
        ]

    def _history_view(self) -> str:
        """The history the planner sees: recent steps in full, older ones
        collapsed to a count so a long run can't blow the context or the budget."""
        if len(self._history) <= _HISTORY_WINDOW:
            return "\n".join(self._history) or "(nothing yet)"
        omitted = len(self._history) - _HISTORY_WINDOW
        recent = self._history[-_HISTORY_WINDOW:]
        return f"[... {omitted} earlier steps omitted ...]\n" + "\n".join(recent)

    async def _finish(self, task: TaskModel, reason: StopReason) -> None:
        task.status = TaskStatus.COMPLETED.value
        task.stop_reason = reason.value
        await self._commit()
        log.info(
            "agent.finish", task_id=str(task.id), reason=reason.value,
            steps=task.steps_used, tokens=task.tokens_used, score=task.verification_score,
        )

    async def _commit(self) -> None:
        await self.session.commit()
