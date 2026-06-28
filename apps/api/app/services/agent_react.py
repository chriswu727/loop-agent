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
from app.services.ledger import genesis_hash, step_hash
from app.services.prompts import plan_prompts, understand_prompts, verify_prompts
from app.services.receipt import build_receipt
from app.services.verification import checks_summary, run_checks
from app.tools import VALID_TOOLS, CapabilityEnvelope, ToolExecutor, ToolStatus, Workspace
from app.tools.guards import make_egress_guard
from app.tools.policy import Verdict, evaluate_command

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
        self._last_hash = ""  # head of the step hash chain

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
        envelope = CapabilityEnvelope.from_tools(
            task.allowed_tools, egress_allowed=task.allow_egress
        )
        executor = ToolExecutor(
            workspace,
            approval_mode=settings.agent_approval_mode,
            command_timeout=settings.agent_command_timeout_seconds,
            output_limit=settings.agent_command_output_limit,
            envelope=envelope,
            before_tool=make_egress_guard(envelope),
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

        # Resuming from an approved action: run it now as this step, then continue.
        if task.pending_action is not None:
            action = dict(task.pending_action)
            task.pending_action = None
            result = await executor.execute(str(action["tool"]), dict(action.get("args", {})))
            await self._record_step(
                task, start, "(approved by the user)", str(action["tool"]),
                dict(action.get("args", {})), result.observation, result.status, 0,
            )
            start += 1
            if start > task.max_steps:
                await self._finish(task, StopReason.MAX_STEPS)
                return

        approval_required = task.require_approval or settings.agent_approval_mode == "manual"
        consecutive_failures = 0
        finish_retries = 0
        # Repeated writes to one file without running it = no progress; nudge on
        # the 2nd, hard-block the 3rd so the model is forced to make progress.
        same_path_writes = 0
        last_write_path: str | None = None

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
                executor.envelope.restricted_executor_tools(),
                executor.envelope.egress_allowed,
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

            # No-progress guard: a model that rewrites the same file again and
            # again without running it is spinning. Nudge on the 2nd repeat, then
            # HARD-BLOCK the 3rd+ so it is forced to run the file or do something
            # else — turning a stuck loop into forward progress.
            if tool in ("write_file", "edit_file"):
                path = str(args.get("path", ""))
                same_path_writes = same_path_writes + 1 if path == last_write_path else 1
                last_write_path = path
            elif tool is not None:
                same_path_writes = 0
                last_write_path = None

            if tool is None:
                observation, status = (
                    "Could not parse a valid action. Respond with one JSON object "
                    f"using a valid tool: {sorted(VALID_TOOLS)}.",
                    ToolStatus.ERROR,
                )
            elif tool in ("write_file", "edit_file") and same_path_writes >= 3:
                observation, status = (
                    f"Blocked: you have written '{last_write_path}' {same_path_writes} times "
                    "without running it. Writing it again is not allowed — run it with "
                    "run_command, call finish with checks, or take a different action.",
                    ToolStatus.BLOCKED,
                )
            elif tool == "run_command" and approval_required:
                verdict, reason = evaluate_command(str(args.get("command", "")))
                if verdict is Verdict.NEEDS_APPROVAL:
                    await self._pause_for_approval(task, args, thought, number, step_tokens, reason)
                    return  # resumes when the user approves or denies
                tool_result = await executor.execute(tool, args)
                observation, status = tool_result.observation, tool_result.status
            else:
                tool_result = await executor.execute(tool, args)
                observation, status = tool_result.observation, tool_result.status
                if tool in ("write_file", "edit_file") and same_path_writes == 2:
                    observation += (
                        "\n[Run this file (run_command) or call finish with checks; "
                        "do not rewrite it again.]"
                    )

            await self._record_step(task, number, thought, tool or "invalid", args,
                                    observation, status, step_tokens)

            stalled = status is not ToolStatus.OK or same_path_writes >= 2
            consecutive_failures = consecutive_failures + 1 if stalled else 0

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

    async def _pause_for_approval(
        self, task: TaskModel, args: dict[str, Any], thought: str,
        number: int, tokens: int, reason: str,
    ) -> None:
        """Pause before running a non-allowlisted command until the user approves."""
        command = str(args.get("command", "")).strip()
        await self._record_step(
            task, number, thought, "run_command", args,
            f"Paused — needs your approval to run this command ({reason}).",
            ToolStatus.BLOCKED, tokens,
        )
        task.pending_action = {"tool": "run_command", "args": args}
        task.pending_question = (
            f"Approve running this command? Answer yes or no.\n  {command}\n  (reason: {reason})"
        )
        task.status = TaskStatus.AWAITING_INPUT.value
        await self._commit()
        log.info("agent.awaiting_approval", task_id=str(task.id), number=number)

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
        """Verify a finish attempt. Re-runs any machine checks the agent attached
        on a fresh copy of the workspace, then asks the verifier for a grounded
        verdict. Returns (accepted, score, summary, verify_tokens)."""
        summary = str(args.get("summary", "")).strip() or "(no summary provided)"
        raw_checks = args.get("checks")
        checks = (
            [c for c in raw_checks if isinstance(c, dict)] if isinstance(raw_checks, list) else []
        )

        check_results = await run_checks(
            checks, workspace,
            approval_mode=settings.agent_approval_mode,
            command_timeout=settings.agent_command_timeout_seconds,
            output_limit=settings.agent_command_output_limit,
        )
        checks_passed = all(r.passed for r in check_results) if check_results else None
        verified_by = "execution" if check_results else "judgment"

        system, user = verify_prompts(
            task.goal, task.rubric, summary, workspace.tree(), checks_summary(check_results)
        )
        result = await self.llm.complete(system, user, max_tokens=500, temperature=0.2)
        parsed = _extract_json(result.content)
        if isinstance(parsed, dict):
            score = _clamp_score(parsed.get("score"))
            missing = parsed.get("missing") or []
            llm_met = bool(parsed.get("met"))
        else:
            score, missing, llm_met = 0, ["verifier returned no verdict"], False

        # A run with checks is accepted only if its checks actually pass; a run
        # without checks falls back to judgment (and is labelled as such).
        met = llm_met and score >= settings.agent_acceptance_score
        if check_results and not checks_passed:
            met = False

        verdict = f"verifier: score {score}, met={met}, verified_by={verified_by}"
        if check_results:
            verdict += "\nchecks:\n" + checks_summary(check_results)
        if missing:
            verdict += "\nmissing:\n" + "\n".join(f"- {m}" for m in missing)
        await self._record_step(task, number, thought, "finish", args, verdict,
                                ToolStatus.OK, plan_tokens + result.tokens)

        if met:
            task.summary = summary
            task.verification_score = score
            task.verified_by = verified_by
            receipt_hash, _ = build_receipt(
                task, check_results, score=score, verified_by=verified_by,
                workspace=workspace, ledger_head=self._last_hash,
            )
            task.receipt_hash = receipt_hash
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
        prev_hash = self._last_hash
        this_hash = step_hash(
            prev_hash, number=number, tool=tool, tool_args=args,
            observation=observation, status=status.value, tokens=tokens,
        )
        await self.steps.create(
            task_id=task.id,
            number=number,
            thought=thought,
            tool=tool,
            tool_args=args,
            observation=observation,
            status=status.value,
            tokens=tokens,
            prev_hash=prev_hash,
            hash=this_hash,
        )
        self._last_hash = this_hash
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
        """Reconstruct working memory (and the chain head) from persisted steps."""
        steps = await self.steps.list_for_task(task_id)
        self._history = [
            self._format_history(
                s.number, s.thought, s.tool, s.tool_args, s.observation, ToolStatus(s.status)
            )
            for s in steps
        ]
        self._last_hash = steps[-1].hash if steps else genesis_hash(task_id)

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
