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

import asyncio
import json
import re
import shutil
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.llm import LLMClient, LLMError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.db.models.task import TaskModel
from app.domain.authority_token import (
    EGRESS_PROXY_AUDIENCE,
    AuthorityTokenError,
    intersect_host_policies,
    issue_authority_token,
    normalize_hosts,
)
from app.domain.capability import (
    Capability,
    legacy_capabilities,
    parse_capabilities,
    sorted_capabilities,
)
from app.domain.task import StopReason, TaskStatus
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.ledger import genesis_hash, step_hash
from app.services.memory import MemoryStore, scoped_memory_root
from app.services.prompts import plan_prompts, understand_prompts, verify_prompts
from app.services.receipt import RECEIPT_SCHEMA, build_receipt
from app.services.skills import SkillStore
from app.services.verification import checks_summary, execution_coverage_complete, run_checks
from app.tools import VALID_TOOLS, CapabilityEnvelope, ToolExecutor, ToolStatus, Workspace
from app.tools.calendar import CalendarTools
from app.tools.egress import EgressAuditClient
from app.tools.email import EmailTools
from app.tools.guards import make_egress_guard
from app.tools.mcp import McpBrowser
from app.tools.policy import Verdict, evaluate_command
from app.tools.provider_gateway import ProviderGatewayClient
from app.tools.registry import CALENDAR_SPEC, EMAIL_SPEC, VISION_SPEC
from app.tools.sandbox import docker_available, image_present
from app.tools.vision import VisionTools

log = get_logger("agent")

# How many recent steps the planner sees in full; older steps collapse to a count.
_HISTORY_WINDOW = 12

# Below this many tokens left, a spawn is refused rather than floored — flooring
# would let the sub-tree overshoot the parent's (and the global) token ceiling.
_MIN_SPAWN_BUDGET = 1_000

# A reasoning model (deepseek-reasoner) spends the max_tokens budget on its chain of
# thought BEFORE the answer, so a tight cap returns finish_reason=length with an EMPTY
# content field — the decision/verdict is never emitted. That surfaced as intermittent
# "invalid action" steps and, worse, verifier verdicts silently defaulting to score 0
# (rejecting valid work). Budget generously for CoT + the answer. max_tokens is a
# ceiling, not a target: a non-reasoning model still stops at its short answer, so the
# larger cap costs it nothing.
_PLAN_MAX_TOKENS = 2_500  # a plan decision can also carry write_file content
_VERDICT_MAX_TOKENS = 1_500  # rubric + verify: reasoning then a short JSON verdict


def _balanced_json_objects(text: str) -> list[str]:
    """Every balanced-brace ``{...}`` substring, in order, ignoring braces inside
    string literals. A greedy ``\\{.*\\}`` regex fails on the two things reasoning
    models do constantly — dict-like braces in prose (``a map {k: v}``) before the
    real JSON, and emitting more than one object — so scan properly instead."""
    spans: list[str] = []
    depth, start = 0, -1
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start : i + 1])
                start = -1
    return spans


def _extract_json(text: str) -> Any:
    """Best-effort: pull the model's JSON decision out of a reply that may be wrapped
    in prose or chain-of-thought. Prefers the LAST parseable object — a reasoning
    model reasons first and states its decision at the end."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for span in reversed(_balanced_json_objects(cleaned)):
        try:
            obj = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _as_int(value: object, default: int) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _clamp_score(value: object) -> int:
    try:
        return max(0, min(100, int(float(value))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class AgentReactService:
    def __init__(
        self,
        tasks: TaskRepository,
        steps: StepRepository,
        llm: LLMClient,
        verifier_llm: LLMClient | None = None,
    ) -> None:
        self.tasks = tasks
        self.steps = steps
        self.llm = llm
        self.verifier_llm = verifier_llm or llm
        self.session = tasks.session
        self._history: list[str] = []
        self._last_hash = ""  # head of the step hash chain
        self.memory = MemoryStore(
            scoped_memory_root(Path(settings.agent_memory_root), "local", "default")
        )
        self._memory_snapshot = ""  # what the agent remembers, injected into planning
        self._skill_instructions = ""  # instructions from the task's signed skill
        self._browser_specs = ""  # MCP browser tool list, injected into planning
        self._notices = ""  # run-time notices for the planner (e.g. a tool went missing)
        self._email_specs = ""  # email tool list, injected into planning
        self._calendar_specs = ""  # calendar tool list, injected into planning
        self._vision_specs = ""  # see_image tool, injected into planning when available
        self._conversation = ""  # earlier turns of a chat/session, injected into planning
        self._mcp_tools: set[str] = set()  # extra tool names the planner may call
        self._sandbox_image: str | None = None  # container image for run_command, or None
        self._sandbox_backend: str | None = None
        self._egress_allowed = False  # resolved egress; verification checks mirror it
        self._authority_token_factory: Callable[[str], str] | None = None

    async def run(self, task_id: uuid.UUID) -> None:
        """Run, or resume, a task. A task is resumable when it was paused on an
        ask_user question and the user has since answered (status back to
        pending with steps already on record)."""
        task = await self.tasks.claim_pending(task_id)
        if task is None:
            existing = await self.tasks.get(task_id)
            if existing is None:
                log.warning("agent.task_missing", task_id=str(task_id))
            else:
                log.info("agent.skip_non_pending", task_id=str(task_id), status=existing.status)
            return

        self._authority_token_factory = None
        self._sandbox_image = None
        self._sandbox_backend = None
        self._egress_allowed = False

        workspace = Workspace(
            Path(task.workspace_path or settings.agent_workspaces_root)
            / ("" if task.workspace_path else str(task.id))
        )
        self.memory = MemoryStore(
            scoped_memory_root(Path(settings.agent_memory_root), task.owner_id, task.project_id)
        )

        # Load the signed skill (if any) BEFORE anything runs. A skill that can't
        # be verified is refused outright — provenance is not optional.
        skill_capabilities: frozenset[Capability] | None = None
        skill_egress_hosts: list[str] | None = None
        self._skill_instructions = ""
        self._mcp_tools = set()
        self._browser_specs = ""
        self._email_specs = ""
        self._calendar_specs = ""
        self._vision_specs = ""
        self._notices = ""
        if task.skill:
            store = SkillStore(Path(settings.agent_skills_root), settings.trust_public_key_pem())
            skill = store.load(task.skill)
            if skill is None:
                task.status = TaskStatus.FAILED.value
                task.stop_reason = StopReason.ERROR.value
                task.error = (
                    f"Skill '{task.skill}' could not be loaded (unsigned, tampered, or "
                    "not found). Refusing to run."
                )
                task.workspace_path = str(workspace.root)  # so the refusal is auditable
                self._ensure_unverified_receipt(task)
                await self._commit()
                log.warning("agent.skill_refused", task_id=str(task.id), skill=task.skill)
                return
            self._skill_instructions = skill.manifest.instructions
            skill_egress_hosts = skill.manifest.egress_hosts
            skill_capabilities = (
                parse_capabilities(skill.manifest.capabilities)
                if skill.manifest.capabilities is not None
                else legacy_capabilities(
                    skill.manifest.allowed_tools,
                    allow_egress=skill.manifest.allow_egress,
                    use_browser=False,
                    use_email=False,
                    use_calendar=False,
                )
            )

        requested_capabilities = (
            parse_capabilities(task.requested_capabilities)
            if task.requested_capabilities is not None
            else legacy_capabilities(
                task.allowed_tools,
                allow_egress=task.allow_egress,
                use_browser=task.use_browser,
                use_email=task.use_email,
                use_calendar=task.use_calendar,
                use_vision=task.use_vision,
            )
        )
        resolved_capabilities = (
            requested_capabilities
            if skill_capabilities is None
            else requested_capabilities & skill_capabilities
        )
        try:
            requested_egress_hosts = normalize_hosts(task.egress_hosts or [])
            resolved_egress_hosts = (
                requested_egress_hosts
                if skill_egress_hosts is None
                else intersect_host_policies(requested_egress_hosts, skill_egress_hosts)
            )
        except AuthorityTokenError as exc:
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = f"Invalid destination authority: {exc}"
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return
        task.egress_hosts = sorted(resolved_egress_hosts)
        envelope = CapabilityEnvelope.from_capabilities(
            resolved_capabilities,
            egress_hosts=task.egress_hosts,
        )
        task.resolved_capabilities = sorted_capabilities(resolved_capabilities)
        provider_capabilities = {
            Capability.NET_BROWSER,
            Capability.EMAIL_READ,
            Capability.EMAIL_SEND,
            Capability.CALENDAR_READ,
            Capability.CALENDAR_WRITE,
            Capability.VISION,
        }
        destination_capabilities = {Capability.NET_SHELL, Capability.NET_BROWSER}
        if (
            settings.agent_require_egress_hosts
            and resolved_capabilities & destination_capabilities
            and not envelope.egress_hosts
        ):
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = "Shell/browser network authority requires explicit egress_hosts."
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return
        if (
            resolved_capabilities & provider_capabilities
            and not settings.agent_provider_gateway_url
            and not settings.agent_allow_host_providers
        ):
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = (
                "A requested provider capability is disabled because no isolated "
                "provider gateway is configured."
            )
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return
        self._egress_allowed = envelope.egress_allowed
        try:
            sandbox_image, sandbox_label, sandbox_backend = self._resolve_sandbox()
        except RuntimeError as exc:
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = str(exc)
            task.sandbox = "unavailable"
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return
        self._sandbox_image = sandbox_image
        self._sandbox_backend = sandbox_backend
        task.sandbox = sandbox_label
        if Capability.NET_SHELL in resolved_capabilities and (
            sandbox_label == "inline"
            or not settings.agent_egress_proxy_url
            or not settings.agent_egress_proxy_audit_url
        ):
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = (
                "Shell network authority requires an isolated sandbox and "
                "destination-enforcing egress proxy."
            )
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return

        uses_gateway = bool(
            resolved_capabilities & provider_capabilities and settings.agent_provider_gateway_url
        )
        needs_authority_token = uses_gateway or Capability.NET_SHELL in resolved_capabilities
        authority_key = settings.authority_signing_key_pem()
        if needs_authority_token and not authority_key:
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = "Isolated network/provider execution requires an authority signing key."
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return

        run_id = f"{task.id}:{task.attempt}"

        def token_factory(audience: str) -> str:
            if not authority_key:
                raise RuntimeError("Authority signing key is unavailable")
            return issue_authority_token(
                authority_key,
                audience=audience,
                task_id=str(task.id),
                owner_id=task.owner_id,
                project_id=task.project_id,
                run_id=run_id,
                capabilities=resolved_capabilities,
                egress_hosts=task.egress_hosts or [],
                ttl_seconds=settings.agent_authority_token_ttl_seconds,
            )

        self._authority_token_factory = token_factory if needs_authority_token else None
        provider_gateway: ProviderGatewayClient | None = None
        egress_audit = (
            EgressAuditClient(settings.agent_egress_proxy_audit_url, token_factory)
            if resolved_capabilities & destination_capabilities
            and settings.agent_egress_proxy_audit_url
            else None
        )
        if not isinstance(task.authority_audit, list):
            task.authority_audit = []

        async def collect_authority_audit(tool: str, _args: dict[str, Any], _result: Any) -> None:
            events: list[dict[str, Any]] = []
            if provider_gateway is not None:
                events.extend(provider_gateway.drain_audit())
            network_tool = tool == "run_command" or (
                provider_gateway is not None and tool in provider_gateway.tool_names
            )
            if network_tool and egress_audit is not None:
                try:
                    events.extend(await egress_audit.fetch_new())
                except Exception as exc:
                    events.append(
                        {
                            "kind": "audit",
                            "decision": "unavailable",
                            "run_id": run_id,
                            "error": type(exc).__name__,
                        }
                    )
            if events:
                task.authority_audit = [*(task.authority_audit or []), *events][-200:]

        executor = ToolExecutor(
            workspace,
            approval_mode=settings.agent_approval_mode,
            command_timeout=settings.agent_command_timeout_seconds,
            output_limit=settings.agent_command_output_limit,
            envelope=envelope,
            before_tool=make_egress_guard(envelope, workspace),
            after_tool=collect_authority_audit,
            sandbox_image=sandbox_image,
            sandbox_backend=sandbox_backend,
            sandbox_memory=settings.agent_sandbox_memory,
            sandbox_cpus=settings.agent_sandbox_cpus,
            egress_proxy_url=settings.agent_egress_proxy_url,
            egress_network=settings.agent_egress_docker_network,
            egress_token_factory=(
                lambda: (
                    token_factory(EGRESS_PROXY_AUDIENCE)
                    if Capability.NET_SHELL in resolved_capabilities
                    else ""
                )
            ),
            docker_workspace_volume=settings.agent_docker_workspace_volume,
            docker_workspace_mount=settings.agent_docker_workspace_mount,
        )

        browser: McpBrowser | None = None
        if uses_gateway:
            provider_gateway = ProviderGatewayClient(
                settings.agent_provider_gateway_url or "",
                workspace,
                token_factory,
                timeout_seconds=settings.agent_command_timeout_seconds + 15,
            )
            try:
                await provider_gateway.start()
            except Exception as exc:
                task.status = TaskStatus.FAILED.value
                task.stop_reason = StopReason.ERROR.value
                task.error = f"Provider Gateway is unavailable: {str(exc)[:300]}"
                task.workspace_path = str(workspace.root)
                self._ensure_unverified_receipt(task)
                await self._commit()
                return
            executor.provider_gateway = provider_gateway
            self._mcp_tools |= provider_gateway.tool_names
            self._browser_specs = provider_gateway.specs("net.browser")
            self._email_specs = provider_gateway.specs("email.")
            self._calendar_specs = provider_gateway.specs("calendar.")
            self._vision_specs = provider_gateway.specs("vision")
            required_tools = {
                Capability.EMAIL_READ: "read_inbox",
                Capability.EMAIL_SEND: "send_email",
                Capability.CALENDAR_READ: "list_events",
                Capability.CALENDAR_WRITE: "create_event",
                Capability.VISION: "see_image",
            }
            missing = [
                capability.value
                for capability, tool in required_tools.items()
                if capability in resolved_capabilities and tool not in provider_gateway.tool_names
            ]
            if Capability.NET_BROWSER in resolved_capabilities and not self._browser_specs.strip():
                missing.append(Capability.NET_BROWSER.value)
            if missing:
                await provider_gateway.stop()
                task.status = TaskStatus.FAILED.value
                task.stop_reason = StopReason.ERROR.value
                task.error = f"Provider Gateway lacks requested capabilities: {', '.join(missing)}"
                task.workspace_path = str(workspace.root)
                self._ensure_unverified_receipt(task)
                await self._commit()
                return
        else:
            browser = await self._start_browser(envelope)
            if browser is not None:
                executor.mcp = browser
                self._browser_specs = browser.specs()
                self._mcp_tools |= set(browser.tool_names)
            elif envelope.permits_capability(Capability.NET_BROWSER):
                task.status = TaskStatus.FAILED.value
                task.stop_reason = StopReason.ERROR.value
                task.error = "The requested browser capability is unavailable."
                task.workspace_path = str(workspace.root)
                self._ensure_unverified_receipt(task)
                await self._commit()
                return

        wants_email = envelope.permits_capability(
            Capability.EMAIL_READ
        ) or envelope.permits_capability(Capability.EMAIL_SEND)
        if provider_gateway is not None:
            pass
        elif wants_email and settings.email_configured:
            executor.email = EmailTools()
            self._mcp_tools |= EmailTools.tool_names
            self._email_specs = EMAIL_SPEC
        elif wants_email:
            if browser is not None:
                await browser.stop()
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = "The requested email capability is not configured."
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return

        wants_calendar = envelope.permits_capability(
            Capability.CALENDAR_READ
        ) or envelope.permits_capability(Capability.CALENDAR_WRITE)
        if provider_gateway is not None:
            pass
        elif wants_calendar and settings.calendar_configured:
            executor.calendar = CalendarTools()
            self._mcp_tools |= CalendarTools.tool_names
            self._calendar_specs = CALENDAR_SPEC
        elif wants_calendar:
            if browser is not None:
                await browser.stop()
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = "The requested calendar capability is not configured."
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return

        # Email/calendar talk to the network on the host, OUTSIDE the container's
        # --network none jail — an out-of-sandbox path. Make it explicit rather than
        # silent: every send/create still pauses for your approval, so don't route
        # secrets out this way. Recorded on the task so an auditor sees the exception.
        out_of_sandbox = [
            n for n, on in (("email", executor.email), ("calendar", executor.calendar)) if on
        ]
        if out_of_sandbox:
            self._notices += (
                f"Note: your {' and '.join(out_of_sandbox)} tool(s) reach the network on the "
                "host, OUTSIDE the container sandbox. Every send/create pauses for the user's "
                "approval; never email or export secrets or workspace credentials.\n"
            )

        if provider_gateway is not None:
            pass
        elif envelope.permits_capability(Capability.VISION) and settings.gemini_api_key:
            executor.vision = VisionTools(workspace)
            self._mcp_tools |= VisionTools.tool_names
            self._vision_specs = VISION_SPEC
        elif envelope.permits_capability(Capability.VISION):
            if browser is not None:
                await browser.stop()
            task.status = TaskStatus.FAILED.value
            task.stop_reason = StopReason.ERROR.value
            task.error = "The requested vision capability is not configured."
            task.workspace_path = str(workspace.root)
            self._ensure_unverified_receipt(task)
            await self._commit()
            return

        task.status = TaskStatus.RUNNING.value
        task.workspace_path = str(workspace.root)
        # Rebuild the working memory from whatever has already happened so a
        # resumed run sees its own past actions (and the user's answer).
        await self._rebuild_history(task.id)
        self._memory_snapshot = (
            self.memory.snapshot() if envelope.permits_capability(Capability.MEMORY_READ) else ""
        )
        self._conversation = await self._build_conversation(task)  # earlier turns of this chat
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
            self._ensure_unverified_receipt(task)  # a crash is auditable too
            await self._commit()
        finally:
            if browser is not None:
                await browser.stop()
            if provider_gateway is not None:
                await provider_gateway.stop()

    def _resolve_sandbox(self) -> tuple[str | None, str, str | None]:
        """(image, label): which sandbox to use for run_command, and how to label
        it. 'container' jails commands in Docker; 'inline' runs on the host (a
        clearly-labeled reduced-isolation downgrade when Docker is unavailable)."""
        if settings.agent_sandbox in {"off", "inline"}:
            return None, "inline", None
        image = settings.agent_sandbox_image
        if settings.agent_sandbox_image_digest:
            image = f"{image.split('@', 1)[0]}@{settings.agent_sandbox_image_digest}"
        backend = settings.agent_sandbox_backend
        if backend == "kubernetes" or (
            backend == "auto"
            and Path("/var/run/secrets/kubernetes.io/serviceaccount/token").is_file()
        ):
            return image, "kubernetes", "kubernetes"
        if docker_available() and image_present(image):
            return image, "container", "docker"
        if settings.agent_sandbox in {"required", "container"}:
            raise RuntimeError(
                f"Required sandbox image {image!r} is unavailable; refusing host execution."
            )
        log.warning("agent.sandbox_downgrade", wanted=settings.agent_sandbox)
        return None, "inline", None

    async def _start_browser(self, envelope: CapabilityEnvelope) -> McpBrowser | None:
        if not (
            envelope.permits_capability(Capability.NET_BROWSER) and settings.agent_browser_enabled
        ):
            return None
        browser = McpBrowser(settings.agent_browser_command)
        try:
            await browser.start()
            return browser
        except Exception:  # browser unavailable -> run without it, don't fail the task
            log.warning("agent.browser_unavailable")
            await browser.stop()
            return None

    async def _run_loop(
        self, task: TaskModel, workspace: Workspace, executor: ToolExecutor, *, start: int
    ) -> None:
        if not task.rubric:  # only on a fresh run, not a resume
            try:
                rubric, tokens = await self._understand(task.goal)
            except LLMError as exc:
                # A transient blip on the very first call shouldn't kill the task —
                # fall back to a generic rubric; the plan phase (with retry) does the
                # real work and the verifier still grades against the goal.
                log.warning("agent.understand_failed", task_id=str(task.id), error=str(exc)[:200])
                rubric, tokens = ["Fully and correctly satisfies the task"], 0
            task.rubric = rubric
            task.tokens_used += tokens
            await self._commit()

        # Resuming from an approved action: run it now as this step, then continue.
        if task.pending_action is not None:
            action = dict(task.pending_action)
            task.pending_action = None
            result = await executor.execute(str(action["tool"]), dict(action.get("args", {})))
            await self._record_step(
                task,
                start,
                "(approved by the user)",
                str(action["tool"]),
                dict(action.get("args", {})),
                result.observation,
                result.status,
                0,
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
                # A cancel is a terminal outcome — leave the same auditable artifacts
                # (summary + unverified Receipt of the partial work) as any other stop.
                if not task.summary:
                    task.summary = self._stop_summary(task, StopReason.CANCELLED)
                self._ensure_unverified_receipt(task)
                await self._commit()
                return
            if task.tokens_used >= task.token_budget:
                await self._finish(task, StopReason.BUDGET_EXHAUSTED)
                return

            tokens_left = max(0, task.token_budget - task.tokens_used)
            system, user = plan_prompts(
                task.goal,
                task.rubric,
                workspace.tree(),
                self._history_view(),
                task.max_steps - number + 1,
                tokens_left,
                executor.envelope.restricted_executor_tools(),
                executor.envelope.egress_allowed,
                self._memory_snapshot,
                self._skill_instructions,
                self._browser_specs,
                self._email_specs,
                self._calendar_specs,
                self._vision_specs,
                self._conversation,
                notices=self._notices,
                allow_spawn=task.depth < settings.agent_max_spawn_depth,
                today=date.today().isoformat(),
            )
            decision = await self.llm.complete(
                system, user, max_tokens=_PLAN_MAX_TOKENS, temperature=0.5
            )
            step_tokens = decision.tokens
            thought, tool, args = self._parse_decision(_extract_json(decision.content))

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

            if tool in {"remember", "spawn"} and not executor.envelope.permits(tool):
                await self._record_step(
                    task,
                    number,
                    thought,
                    tool,
                    args,
                    f"Tool '{tool}' is not permitted by this task's capability envelope.",
                    ToolStatus.BLOCKED,
                    step_tokens,
                )
                consecutive_failures += 1
                if number >= task.max_steps:
                    await self._finish(task, StopReason.MAX_STEPS)
                    return
                if consecutive_failures >= settings.agent_stuck_threshold:
                    await self._finish(task, StopReason.STUCK)
                    return
                continue

            if tool == "remember":
                note = str(args.get("note", "")).strip()
                topic = args.get("topic")
                observation = self.memory.remember(note, str(topic) if topic else None)
                if note:  # make it visible to the rest of this run too
                    self._memory_snapshot = f"{self._memory_snapshot}\n- {note}".strip()
                await self._record_step(
                    task, number, thought, "remember", args, observation, ToolStatus.OK, step_tokens
                )
                if number >= task.max_steps:
                    await self._finish(task, StopReason.MAX_STEPS)
                    return
                continue

            if tool == "spawn":
                await self._handle_spawn(task, args, thought, number, step_tokens)
                if number >= task.max_steps:
                    await self._finish(task, StopReason.MAX_STEPS)
                    return
                if task.tokens_used >= task.token_budget:
                    await self._finish(task, StopReason.BUDGET_EXHAUSTED)
                    return
                continue

            if tool == "send_email":
                # Sending is irreversible and external, so it always pauses for a
                # human yes/no before it runs (regardless of approval mode).
                to = str(args.get("to", "")).strip()
                subject = str(args.get("subject", "")).strip()
                await self._pause_for_action(
                    task,
                    "send_email",
                    args,
                    thought,
                    number,
                    step_tokens,
                    f"send an email to {to} (subject: {subject!r})",
                )
                return  # resumes when the user approves or denies

            if tool == "create_event":
                # Writing to the real calendar is side-effecting: always approve.
                summary = str(args.get("summary", "")).strip()
                await self._pause_for_action(
                    task,
                    "create_event",
                    args,
                    thought,
                    number,
                    step_tokens,
                    f"add a calendar event {summary!r} at {args.get('start', '?')}",
                )
                return  # resumes when the user approves or denies

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

            await self._record_step(
                task, number, thought, tool or "invalid", args, observation, status, step_tokens
            )

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

        # The loop exhausted its steps without any branch reaching a terminal
        # state (e.g. a finish rejected on the very last step `continue`d). Never
        # leave a task stuck RUNNING — treat it as hitting the step cap.
        await self.session.refresh(task)
        if task.status == TaskStatus.RUNNING.value:
            await self._finish(task, StopReason.MAX_STEPS)

    async def _pause_for_user(
        self, task: TaskModel, args: dict[str, Any], thought: str, number: int, tokens: int
    ) -> None:
        question = str(args.get("question", "")).strip() or "(the agent asked a question)"
        await self._record_step(
            task,
            number,
            thought,
            "ask_user",
            {"question": question},
            "Waiting for the user's answer.",
            ToolStatus.OK,
            tokens,
        )
        task.pending_question = question
        task.status = TaskStatus.AWAITING_INPUT.value
        await self._commit()
        log.info("agent.awaiting_input", task_id=str(task.id), number=number)

    async def _pause_for_approval(
        self,
        task: TaskModel,
        args: dict[str, Any],
        thought: str,
        number: int,
        tokens: int,
        reason: str,
    ) -> None:
        """Pause before running a non-allowlisted command until the user approves."""
        command = str(args.get("command", "")).strip()
        await self._pause_for_action(
            task,
            "run_command",
            args,
            thought,
            number,
            tokens,
            f"run: {command} (reason: {reason})",
        )

    async def _pause_for_action(
        self,
        task: TaskModel,
        tool: str,
        args: dict[str, Any],
        thought: str,
        number: int,
        tokens: int,
        summary: str,
    ) -> None:
        """Pause a side-effecting action until the user approves; resumes by
        running the stored pending_action when they answer yes."""
        await self._record_step(
            task,
            number,
            thought,
            tool,
            args,
            f"Paused — needs your approval to {summary}.",
            ToolStatus.BLOCKED,
            tokens,
        )
        task.pending_action = {"tool": tool, "args": args}
        task.pending_question = f"Approve this action? Answer yes or no.\n  {summary}"
        task.status = TaskStatus.AWAITING_INPUT.value
        await self._commit()
        log.info("agent.awaiting_approval", task_id=str(task.id), number=number, tool=tool)

    async def _handle_spawn(
        self, task: TaskModel, args: dict[str, Any], thought: str, number: int, plan_tokens: int
    ) -> None:
        """Delegate a sub-goal to a fresh sub-agent: it runs its own verified,
        sandboxed loop under a sub-budget, and its result + output files come back
        as this step's observation. The child's tokens count against this task."""
        if task.depth >= settings.agent_max_spawn_depth:
            await self._record_step(
                task,
                number,
                thought,
                "spawn",
                args,
                f"Blocked: sub-agent depth limit ({settings.agent_max_spawn_depth}) reached. "
                "Do this part yourself.",
                ToolStatus.BLOCKED,
                plan_tokens,
            )
            return
        goal = str(args.get("goal", "")).strip()
        if len(goal) < 4:
            await self._record_step(
                task,
                number,
                thought,
                "spawn",
                args,
                "spawn needs a 'goal' describing the sub-task.",
                ToolStatus.ERROR,
                plan_tokens,
            )
            return

        remaining = max(0, task.token_budget - task.tokens_used - plan_tokens)
        if remaining < _MIN_SPAWN_BUDGET:
            # Flooring the child at 1000 here would let the sub-tree overshoot the
            # parent's (and the global) token ceiling. Refuse instead.
            await self._record_step(
                task,
                number,
                thought,
                "spawn",
                args,
                f"Blocked: only {remaining} tokens left — not enough to delegate. "
                "Do this part yourself.",
                ToolStatus.BLOCKED,
                plan_tokens,
            )
            return
        # Never exceed what the parent actually has left, so the ceiling holds.
        child_budget = max(1, min(_as_int(args.get("token_budget"), remaining), remaining))
        child_steps = max(
            1,
            min(
                _as_int(args.get("max_steps"), settings.agent_max_steps_default),
                settings.agent_max_steps_cap,
            ),
        )
        allowed = args.get("allowed_tools")
        raw_capabilities = args.get("capabilities")
        if isinstance(raw_capabilities, list):
            try:
                child_requested = parse_capabilities(str(value) for value in raw_capabilities)
            except ValueError:
                child_requested = frozenset()
        else:
            child_requested = legacy_capabilities(
                allowed if isinstance(allowed, list) else None,
                allow_egress=bool(args.get("allow_egress", False)),
                use_browser=bool(args.get("use_browser", False)),
                use_email=bool(args.get("use_email", False)),
                use_calendar=bool(args.get("use_calendar", False)),
                use_vision=bool(args.get("use_vision", False)),
            )
        parent_capabilities = parse_capabilities(task.resolved_capabilities or [])
        child_capabilities = child_requested & parent_capabilities
        raw_hosts = args.get("egress_hosts")
        try:
            requested_hosts = normalize_hosts(
                (str(value) for value in raw_hosts) if isinstance(raw_hosts, list) else []
            )
        except ValueError:
            requested_hosts = frozenset()
        parent_hosts = frozenset(task.egress_hosts or [])
        child_hosts = sorted(
            host
            for host in requested_hosts
            if any(host == allowed or host.endswith(f".{allowed}") for allowed in parent_hosts)
        )
        if child_capabilities & {Capability.NET_SHELL, Capability.NET_BROWSER} and not child_hosts:
            await self._record_step(
                task,
                number,
                thought,
                "spawn",
                args,
                "Blocked: a networked sub-agent requires egress_hosts within the "
                "parent task's destination allowlist.",
                ToolStatus.BLOCKED,
                plan_tokens,
            )
            return
        child = await self.tasks.create(
            goal=goal,
            owner_id=task.owner_id,
            project_id=task.project_id,
            status=TaskStatus.PENDING.value,
            rubric=[],
            requested_capabilities=sorted_capabilities(child_capabilities),
            resolved_capabilities=[],
            allowed_tools=allowed if isinstance(allowed, list) else None,
            allow_egress=Capability.NET_SHELL in child_capabilities,
            egress_hosts=child_hosts or None,
            require_approval=task.require_approval,
            use_browser=Capability.NET_BROWSER in child_capabilities,
            skill=None,
            parent_id=task.id,
            depth=task.depth + 1,
            max_steps=child_steps,
            token_budget=child_budget,
            summary=None,
            verification_score=0,
            steps_used=0,
            tokens_used=0,
            workspace_path=None,
        )
        await self._commit()

        child_service = AgentReactService(
            self.tasks, self.steps, self.llm, verifier_llm=self.verifier_llm
        )
        await child_service.run(child.id)
        await self.session.refresh(child)

        task.tokens_used += child.tokens_used  # fold the child's cost into our ceiling
        where = await self._copy_subtask_outputs(task, child)
        ok = (
            child.status == TaskStatus.COMPLETED.value
            and child.stop_reason == StopReason.GOAL_ACHIEVED.value
        )
        files_line = f"\nIts output files are in {where}/." if where else ""
        observation = (
            f"Sub-agent for '{goal[:60]}' finished: status={child.status}, "
            f"stop={child.stop_reason}, verified_by={child.verified_by}, "
            f"score={child.verification_score}.\nSummary: {child.summary or '(none)'}.{files_line}"
        )
        await self._record_step(
            task,
            number,
            thought,
            "spawn",
            args,
            observation,
            ToolStatus.OK if ok else ToolStatus.ERROR,
            plan_tokens,
        )

    async def _copy_subtask_outputs(self, task: TaskModel, child: TaskModel) -> str | None:
        """Copy the child's workspace into this task's workspace under
        subtasks/<id> so the parent can compose the sub-agent's deliverables."""
        if not (task.workspace_path and child.workspace_path):
            return None
        src = Path(child.workspace_path)
        dest_rel = f"subtasks/{str(child.id)[:8]}"
        dest = Path(task.workspace_path) / dest_rel

        def _copy() -> bool:
            if not src.exists():
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Cache cruft never belongs in the parent and its stale .pyc/.pytest_cache
            # entries cause import mismatches when the parent re-runs tools.
            shutil.copytree(
                src,
                dest,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
            )
            # subtasks/ is an ARCHIVE for the parent to compose from, not a place to
            # re-run tests. Without this, a grafted test_foo.py collides with the
            # parent's own test_foo.py (pytest "import file mismatch"), breaking the
            # parent's entire test collection — a real spawn failure seen under R1.
            conftest = dest.parent / "conftest.py"
            if not conftest.exists():
                conftest.write_text('collect_ignore_glob = ["*"]\n')
            return True

        try:
            copied = await asyncio.to_thread(_copy)
            return dest_rel if copied else None
        except Exception:
            log.warning("agent.subtask_copy_failed", task_id=str(task.id), child=str(child.id))
            return None

    async def _build_conversation(self, task: TaskModel) -> str:
        """Earlier turns of this chat/session (goal -> reply), chronological, so a
        follow-up like 'now also add tests' has the context it refers to."""
        if not task.chat_id:
            return ""
        prior = await self.tasks.recent_for_chat(
            task.chat_id,
            exclude_id=task.id,
            owner_id=task.owner_id,
            limit=5,
        )
        turns = [t for t in reversed(prior) if t.summary]  # chronological, answered only
        if not turns:
            return ""
        return "\n".join(
            f'- You were asked: "{t.goal[:200]}" -> you replied: "{(t.summary or "")[:300]}"'
            for t in turns
        )

    # --- LLM phases -------------------------------------------------------

    async def _understand(self, goal: str) -> tuple[list[str], int]:
        system, user = understand_prompts(goal, self._conversation)
        result = await self.llm.complete(
            system, user, max_tokens=_VERDICT_MAX_TOKENS, temperature=0.4
        )
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
            checks,
            workspace,
            approval_mode=settings.agent_approval_mode,
            command_timeout=settings.agent_command_timeout_seconds,
            output_limit=settings.agent_command_output_limit,
            sandbox_image=self._sandbox_image,
            sandbox_backend=self._sandbox_backend,
            sandbox_memory=settings.agent_sandbox_memory,
            sandbox_cpus=settings.agent_sandbox_cpus,
            egress_allowed=self._egress_allowed,
            envelope=CapabilityEnvelope.from_capabilities(
                task.resolved_capabilities,
                egress_hosts=task.egress_hosts,
            ),
            criterion_count=len(task.rubric or []),
            egress_proxy_url=settings.agent_egress_proxy_url,
            egress_network=settings.agent_egress_docker_network,
            egress_token_factory=(
                lambda: (
                    self._authority_token_factory(EGRESS_PROXY_AUDIENCE)
                    if self._authority_token_factory is not None
                    else ""
                )
            ),
            docker_workspace_volume=settings.agent_docker_workspace_volume,
            docker_workspace_mount=settings.agent_docker_workspace_mount,
        )
        checks_passed = all(r.passed for r in check_results) if check_results else None
        coverage_complete = execution_coverage_complete(check_results, len(task.rubric or []))
        verified_by = "execution" if coverage_complete else "judgment"

        system, user = verify_prompts(
            task.goal,
            task.rubric,
            summary,
            workspace.tree(),
            checks_summary(check_results),
            workspace.contents_digest(),
            today=date.today().isoformat(),
        )
        result = await self.verifier_llm.complete(
            system, user, max_tokens=_VERDICT_MAX_TOKENS, temperature=0.2
        )
        parsed = _extract_json(result.content)
        if isinstance(parsed, dict):
            score = _clamp_score(parsed.get("score"))
            missing = parsed.get("missing") or []
            llm_met = bool(parsed.get("met"))
            # Strict parse: omit -> True (prior behavior), but the stronger label
            # must be EARNED, so a string "false"/"no" or a null doesn't slip through
            # bool() as truthy. Only an explicit true earns it.
            _sub = parsed.get("checks_substantiate", True)
            substantiate = _sub is True or (
                isinstance(_sub, str) and _sub.strip().lower() in {"true", "yes", "1"}
            )
        else:
            score, missing, llm_met, substantiate = 0, ["verifier returned no verdict"], False, True

        # Passing checks that don't actually substantiate the goal (e.g. a tautological
        # `echo hi`) don't earn the stronger "execution" label — degrade to judgment so
        # execution-verified always means "checks that really prove the goal passed".
        if check_results and not substantiate:
            verified_by = "judgment"

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
        await self._record_step(
            task,
            number,
            thought,
            "finish",
            args,
            verdict,
            ToolStatus.OK,
            plan_tokens + result.tokens,
        )

        if met:
            task.summary = summary
            task.verification_score = score
            task.verified_by = verified_by
            receipt_hash, _ = build_receipt(
                task,
                check_results,
                score=score,
                verified_by=verified_by,
                workspace=workspace,
                ledger_head=self._last_hash,
            )
            task.receipt_hash = receipt_hash
            task.receipt_schema = RECEIPT_SCHEMA
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
        if tool not in VALID_TOOLS and tool not in self._mcp_tools:
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
        # Single choke point for observations: redact here so a secret never
        # reaches the model history, the sealed ledger, or the API.
        if settings.agent_redact_secrets:
            observation = redact_secrets(observation)
        prev_hash = self._last_hash
        this_hash = step_hash(
            prev_hash,
            number=number,
            tool=tool,
            tool_args=args,
            observation=observation,
            status=status.value,
            tokens=tokens,
            thought=thought,
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
        number: int,
        thought: str,
        tool: str,
        args: dict[str, Any],
        observation: str,
        status: ToolStatus,
    ) -> str:
        arg_preview = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
        obs = observation if len(observation) <= 600 else observation[:600] + " …[truncated]"
        # Observations are untrusted output, framed as [DATA] so the planner is
        # told not to obey any instructions a tool result might contain.
        return (
            f"Step {number} [{tool}] ({status.value}): {thought}\n"
            f"  args: {arg_preview}\n  -> [DATA] {obs}"
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

    @staticmethod
    def _stop_summary(task: TaskModel, reason: StopReason) -> str:
        """A plain-language account for a stop the agent didn't summarize itself, so
        a non-accepted task isn't just a bare score-0 row with a one-word reason."""
        if reason is StopReason.MAX_STEPS:
            return (
                f"Stopped at the step limit ({task.steps_used}/{task.max_steps}) without a "
                "verified result. Any partial work is in the output files — retry with a "
                "higher step limit if the goal needs more room."
            )
        if reason is StopReason.BUDGET_EXHAUSTED:
            return (
                f"Stopped at the token budget ({task.tokens_used}/{task.token_budget}) without "
                "a verified result. Retry with a higher token budget if the goal needs more."
            )
        if reason is StopReason.STUCK:
            return (
                "Stopped after repeated failed or blocked actions without progress. Check the "
                "steps for the recurring error before retrying."
            )
        if reason is StopReason.CANCELLED:
            return "Cancelled before reaching a verified result."
        return "Stopped without a verified result."

    async def _finish(self, task: TaskModel, reason: StopReason) -> None:
        task.status = TaskStatus.COMPLETED.value
        task.stop_reason = reason.value
        # The accepted path sets its own summary; for the other stops the agent
        # never wrote one, so give the user a reason-specific explanation.
        if not task.summary and reason is not StopReason.GOAL_ACHIEVED:
            task.summary = self._stop_summary(task, reason)
        if reason is not StopReason.GOAL_ACHIEVED:
            self._ensure_unverified_receipt(task)
        await self._commit()
        log.info(
            "agent.finish",
            task_id=str(task.id),
            reason=reason.value,
            steps=task.steps_used,
            tokens=task.tokens_used,
            score=task.verification_score,
        )

    def _ensure_unverified_receipt(self, task: TaskModel) -> None:
        """Build a Receipt for a task that ended without acceptance (a limit stop or
        a crash), so the tamper-evident record + file manifest of the partial work
        exists regardless of outcome — a failure is auditable too. Best-effort: a
        receipt-build error must not mask the real outcome."""
        if task.receipt_hash or not task.workspace_path:
            return
        try:
            if not Path(task.workspace_path).is_dir():
                return
            receipt_hash, _ = build_receipt(
                task,
                [],
                score=task.verification_score,
                verified_by="unverified",
                workspace=Workspace(Path(task.workspace_path)),
                ledger_head=self._last_hash,
            )
            task.receipt_hash = receipt_hash
            task.receipt_schema = RECEIPT_SCHEMA
        except Exception:
            log.warning("agent.receipt_build_failed", task_id=str(task.id))

    async def _commit(self) -> None:
        await self.session.commit()
