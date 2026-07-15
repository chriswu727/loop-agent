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
from app.services.agent_react import AgentReactService, _extract_json
from app.tools import Workspace


def test_extract_json_handles_reasoning_model_output() -> None:
    # Clean JSON and fenced JSON still work.
    assert _extract_json('{"tool":"finish"}')["tool"] == "finish"
    assert _extract_json('```json\n{"tool":"write_file"}\n```')["tool"] == "write_file"
    # Prose with dict-like braces BEFORE the decision (greedy regex used to choke).
    assert _extract_json('a map {k: v} then:\n{"tool":"finish"}')["tool"] == "finish"
    # Multiple objects -> take the LAST (the decision a reasoning model states last).
    assert (
        _extract_json('{"thought":"x"}\nActually:\n{"tool":"run_command"}')["tool"] == "run_command"
    )
    # Braces inside a string literal must not confuse brace-balancing.
    assert _extract_json('{"tool":"write_file","args":{"content":"f() { return {}; }"}}')[
        "tool"
    ] == ("write_file")
    # No JSON at all -> None (the loop then re-prompts).
    assert _extract_json("just reasoning, no json") is None


def test_prompts_inject_the_current_date() -> None:
    # The agent has no clock; a dated report/log needs the date in context (else it
    # guesses its stale training date or, with shell off, has to ask the user).
    from app.services.prompts import plan_prompts, verify_prompts

    _, plan_user = plan_prompts("g", ["c"], "tree", "hist", 5, 1000, today="2026-07-05")
    assert "Today's date is 2026-07-05." in plan_user
    _, verify_user = verify_prompts("g", ["c"], "sum", "tree", "checks", today="2026-07-05")
    assert "Today's date is 2026-07-05." in verify_user
    # Absent when not supplied (no misleading blank date line).
    _, no_date = plan_prompts("g", ["c"], "tree", "hist", 5, 1000)
    assert "Today's date" not in no_date


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


async def _make_task(
    session: AsyncSession,
    *,
    max_steps: int,
    token_budget: int,
    require_approval: bool = False,
    skill: str | None = None,
    depth: int = 0,
):
    repo = TaskRepository(session)
    task = await repo.create(
        goal="do the thing",
        status=TaskStatus.PENDING.value,
        rubric=[],
        require_approval=require_approval,
        skill=skill,
        depth=depth,
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
    monkeypatch.setattr(settings, "agent_memory_root", str(tmp_path / "mem"))


async def test_goal_achieved_when_verifier_accepts_finish(session: AsyncSession) -> None:
    plans = [
        {
            "thought": "write the file",
            "tool": "write_file",
            "args": {"path": "result.txt", "content": "done"},
        },
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


async def test_agent_scopes_protocol_and_browser_gateway_tokens(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.agent_react as agent_module
    from app.domain.authority_token import (
        BROWSER_GATEWAY_AUDIENCE,
        CALENDAR_GATEWAY_AUDIENCE,
        EGRESS_PROXY_AUDIENCE,
        EMAIL_GATEWAY_AUDIENCE,
        PROVIDER_GATEWAY_AUDIENCE,
        VISION_GATEWAY_AUDIENCE,
        verify_authority_token,
    )
    from app.domain.capability import Capability
    from app.services.skills import generate_keypair

    private, public = generate_keypair()
    grants: list[tuple[str, frozenset[Capability], frozenset[str]]] = []

    class GatewaySpy:
        capability = Capability.NET_BROWSER

        def __init__(
            self,
            _base_url: str,
            _workspace: Workspace,
            token_factory: Any,
            *,
            audience: str = PROVIDER_GATEWAY_AUDIENCE,
            egress_authority: bool = False,
            timeout_seconds: int = 65,
        ) -> None:
            del timeout_seconds
            self.token_factory = token_factory
            self.audience = audience
            self.egress_authority = egress_authority
            if audience == BROWSER_GATEWAY_AUDIENCE:
                self.tools = [
                    {
                        "name": "browser_navigate",
                        "description": "Navigate",
                        "capability": "net.browser",
                    }
                ]
            elif audience == EMAIL_GATEWAY_AUDIENCE:
                self.tools = [
                    {
                        "name": "read_inbox",
                        "description": "Read inbox",
                        "capability": "email.read",
                    }
                ]
            elif audience == CALENDAR_GATEWAY_AUDIENCE:
                self.tools = [
                    {
                        "name": "list_events",
                        "description": "List events",
                        "capability": "calendar.read",
                    }
                ]
            else:
                self.tools = [
                    {
                        "name": "see_image",
                        "description": "See image",
                        "capability": "vision",
                    }
                ]
            self.tool_names = {item["name"] for item in self.tools}

        async def start(self) -> None:
            grant = verify_authority_token(
                self.token_factory(self.audience), public, audience=self.audience
            )
            grants.append((self.audience, grant.capabilities, grant.egress_hosts))
            if self.egress_authority:
                egress = verify_authority_token(
                    self.token_factory(EGRESS_PROXY_AUDIENCE),
                    public,
                    audience=EGRESS_PROXY_AUDIENCE,
                )
                grants.append((EGRESS_PROXY_AUDIENCE, egress.capabilities, egress.egress_hosts))

        async def call(self, _name: str, _args: dict[str, Any]) -> str:
            raise AssertionError("No gateway tool should be called")

        async def stop(self) -> None:
            return None

        async def revoke(self) -> dict[str, Any]:
            return {"kind": "authority", "decision": "revoked", "service": self.audience}

        def drain_audit(self) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(agent_module, "ProviderGatewayClient", GatewaySpy)
    monkeypatch.setattr(settings, "agent_provider_gateway_url", None)
    monkeypatch.setattr(settings, "agent_email_gateway_url", "http://email-gateway:8090")
    monkeypatch.setattr(settings, "agent_email_egress_hosts", "mail.example.com")
    monkeypatch.setattr(settings, "agent_calendar_gateway_url", "http://calendar-gateway:8090")
    monkeypatch.setattr(settings, "agent_calendar_egress_hosts", "caldav.example.com")
    monkeypatch.setattr(settings, "agent_vision_gateway_url", "http://vision-gateway:8090")
    monkeypatch.setattr(settings, "agent_vision_egress_hosts", "generativelanguage.googleapis.com")
    monkeypatch.setattr(settings, "agent_browser_gateway_url", "http://browser-gateway:8090")
    monkeypatch.setattr(settings, "agent_allow_host_providers", False)
    monkeypatch.setattr(settings, "agent_authority_signing_key", private)
    monkeypatch.setattr(settings, "agent_authority_signing_key_file", None)
    monkeypatch.setattr(settings, "agent_egress_proxy_audit_url", None)

    task = await TaskRepository(session).create(
        goal="read mail, calendar, image, and browse",
        status=TaskStatus.PENDING.value,
        rubric=[],
        requested_capabilities=[
            "email.read",
            "calendar.read",
            "vision",
            "fs.read",
            "net.browser",
        ],
        egress_hosts=["docs.example.com"],
        max_steps=5,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()
    service = _service(
        session,
        ScriptedLLM([{"thought": "done", "tool": "finish", "args": {"summary": "done"}}]),
    )
    monkeypatch.setattr(service, "_resolve_sandbox", lambda: (None, "inline", None))

    await service.run(task.id)

    assert grants == [
        (
            EMAIL_GATEWAY_AUDIENCE,
            frozenset({Capability.EMAIL_READ}),
            frozenset({"mail.example.com"}),
        ),
        (
            EGRESS_PROXY_AUDIENCE,
            frozenset({Capability.EMAIL_READ}),
            frozenset({"mail.example.com"}),
        ),
        (
            CALENDAR_GATEWAY_AUDIENCE,
            frozenset({Capability.CALENDAR_READ}),
            frozenset({"caldav.example.com"}),
        ),
        (
            EGRESS_PROXY_AUDIENCE,
            frozenset({Capability.CALENDAR_READ}),
            frozenset({"caldav.example.com"}),
        ),
        (
            VISION_GATEWAY_AUDIENCE,
            frozenset({Capability.FS_READ, Capability.VISION}),
            frozenset({"generativelanguage.googleapis.com"}),
        ),
        (
            EGRESS_PROXY_AUDIENCE,
            frozenset({Capability.FS_READ, Capability.VISION}),
            frozenset({"generativelanguage.googleapis.com"}),
        ),
        (
            BROWSER_GATEWAY_AUDIENCE,
            frozenset({Capability.NET_BROWSER}),
            frozenset({"docs.example.com"}),
        ),
        (
            EGRESS_PROXY_AUDIENCE,
            frozenset({Capability.NET_BROWSER}),
            frozenset({"docs.example.com"}),
        ),
    ]


async def test_agent_fails_closed_when_provider_gateway_has_no_host_policy(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "agent_provider_gateway_url", None)
    monkeypatch.setattr(settings, "agent_email_gateway_url", "http://email-gateway:8090")
    monkeypatch.setattr(settings, "agent_email_egress_hosts", "")
    monkeypatch.setattr(settings, "agent_allow_host_providers", False)
    task = await TaskRepository(session).create(
        goal="read mail",
        status=TaskStatus.PENDING.value,
        rubric=[],
        requested_capabilities=["email.read"],
        max_steps=5,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()

    await _service(
        session,
        ScriptedLLM([{"thought": "done", "tool": "finish", "args": {"summary": "done"}}]),
    ).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.FAILED.value
    assert task.error == "Provider gateways require configured egress hosts: Email Gateway"


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
    plans = [
        {"thought": "keep writing", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}}
    ]
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


async def test_repeated_writes_are_hard_blocked_then_recover(session: AsyncSession) -> None:
    # The model rewrites a.txt three times; the 3rd is hard-blocked. It then runs
    # a command and finishes — a stuck loop turned into forward progress.
    plans = [
        {"thought": "w", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {"thought": "w", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {"thought": "w again", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {"thought": "ok run", "tool": "run_command", "args": {"command": "echo done"}},
        {"thought": "done", "tool": "finish", "args": {"summary": "made a.txt"}},
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans, verify={"score": 90, "met": True})).run(task.id)

    steps = await StepRepository(session).list_for_task(task.id)
    third = next(s for s in steps if s.number == 3)
    assert third.tool == "write_file" and third.status == "blocked"
    assert "blocked" in third.observation.lower()

    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value  # recovered, not stuck


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
        {"thought": "need input", "tool": "ask_user", "args": {"question": "Which language?"}},
        {
            "thought": "now build",
            "tool": "write_file",
            "args": {"path": "out.txt", "content": "python"},
        },
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
        {
            "thought": "write it",
            "tool": "write_file",
            "args": {"path": "out.txt", "content": "ready"},
        },
        {
            "thought": "prove it",
            "tool": "finish",
            "args": {
                "summary": "wrote out.txt",
                "checks": [
                    {"kind": "file_exists", "path": "out.txt"},
                    {"kind": "file_contains", "path": "out.txt", "text": "ready"},
                ],
            },
        },
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
    plans = [
        {
            "thought": "claim",
            "tool": "finish",
            "args": {
                "summary": "all good",
                "checks": [{"kind": "file_exists", "path": "does-not-exist.txt"}],
            },
        }
    ]
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans, verify={"score": 99, "met": True, "missing": []})
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    # Even though the LLM said met=true, the failed check kept it from finishing.
    assert task.stop_reason == StopReason.STUCK.value
    assert task.verified_by is None  # never verified — acceptance was blocked
    # It still gets a Receipt, but marked "unverified" (a failure is auditable too).
    assert task.receipt_hash is not None
    receipt = json.loads(Workspace(Path(task.workspace_path)).read("receipt.json"))
    assert receipt["verified_by"] == "unverified"


def _install_signed_skill(tmp_path: Path, monkeypatch, *, name: str, manifest: dict) -> None:
    import json as _json

    from app.core.config import settings as _settings
    from app.services.skills import generate_keypair, sign_skill

    priv, pub = generate_keypair()
    root = tmp_path / "skills"
    d = root / name
    d.mkdir(parents=True)
    (d / "skill.json").write_text(_json.dumps(manifest))
    sign_skill(d, priv)
    monkeypatch.setattr(_settings, "agent_skills_root", str(root))
    monkeypatch.setattr(_settings, "agent_skill_trust_public_key", pub)


async def test_verified_skill_applies_its_envelope(
    session: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    # A skill that only permits write_file -> run_command is blocked by the envelope.
    _install_signed_skill(
        tmp_path,
        monkeypatch,
        name="filer",
        manifest={
            "name": "filer",
            "instructions": "Only write files.",
            "allowed_tools": ["write_file", "read_file"],
            "allow_egress": False,
        },
    )
    plans = [{"thought": "run", "tool": "run_command", "args": {"command": "echo hi"}}]
    task = await _make_task(session, max_steps=8, token_budget=1_000_000, skill="filer")
    await _service(session, ScriptedLLM(plans)).run(task.id)

    steps = await StepRepository(session).list_for_task(task.id)
    assert steps[0].tool == "run_command" and steps[0].status == "blocked"
    assert "envelope" in steps[0].observation.lower()


async def test_verified_skill_narrows_task_destination_policy(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings as _settings
    from app.services.skills import generate_keypair

    _install_signed_skill(
        tmp_path,
        monkeypatch,
        name="api-writer",
        manifest={
            "name": "api-writer",
            "instructions": "Only use the API host.",
            "capabilities": ["fs.write", "net.shell"],
            "egress_hosts": ["api.example.com"],
        },
    )
    private, _public = generate_keypair()
    monkeypatch.setattr(_settings, "agent_authority_signing_key", private)
    monkeypatch.setattr(_settings, "agent_authority_signing_key_file", None)
    monkeypatch.setattr(_settings, "agent_egress_proxy_url", "http://egress-proxy:8080")
    monkeypatch.setattr(_settings, "agent_egress_proxy_audit_url", "http://egress-proxy:8081")

    task = await TaskRepository(session).create(
        goal="write a local result",
        status=TaskStatus.PENDING.value,
        rubric=[],
        requested_capabilities=["fs.write", "net.shell"],
        egress_hosts=["example.com"],
        skill="api-writer",
        max_steps=5,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()
    service = _service(
        session,
        ScriptedLLM(
            [
                {
                    "thought": "write",
                    "tool": "write_file",
                    "args": {"path": "result.txt", "content": "done"},
                },
                {"thought": "done", "tool": "finish", "args": {"summary": "done"}},
            ]
        ),
    )
    monkeypatch.setattr(
        service,
        "_resolve_sandbox",
        lambda: ("loop-sandbox:latest", "container", "docker"),
    )

    await service.run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.egress_hosts == ["api.example.com"]


async def test_unverified_skill_is_refused(
    session: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "agent_skills_root", str(tmp_path / "skills"))
    monkeypatch.setattr(_settings, "agent_skill_trust_public_key", None)
    task = await _make_task(session, max_steps=8, token_budget=1_000_000, skill="ghost")
    llm = ScriptedLLM([{"tool": "finish", "args": {"summary": "x"}}])
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.FAILED.value
    assert "could not be loaded" in (task.error or "")
    assert task.steps_used == 0  # nothing ran
    assert task.receipt_hash  # ...but the refusal is still auditable via a Receipt
    receipt = json.loads(Workspace(Path(task.workspace_path)).read("receipt.json"))
    assert receipt["verified_by"] == "unverified"


async def test_remember_persists_across_tasks(session: AsyncSession) -> None:
    from app.core.config import settings as _settings
    from app.services.memory import MemoryStore, scoped_memory_root

    plans = [
        {
            "thought": "note it",
            "tool": "remember",
            "args": {"note": "The deploy command is make ship"},
        },
        {"thought": "done", "tool": "finish", "args": {"summary": "noted"}},
    ]
    task = await _make_task(session, max_steps=8, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans, verify={"score": 90, "met": True})).run(task.id)

    store = MemoryStore(
        scoped_memory_root(Path(_settings.agent_memory_root), task.owner_id, task.project_id)
    )
    assert "make ship" in store.snapshot()


async def test_approval_off_runs_non_allowlisted_command(session: AsyncSession) -> None:
    plans = [
        {"thought": "run", "tool": "run_command", "args": {"command": "whoami"}},
        {"thought": "done", "tool": "finish", "args": {"summary": "ran it"}},
    ]
    task = await _make_task(session, max_steps=8, token_budget=1_000_000)  # require_approval off
    await _service(session, ScriptedLLM(plans, verify={"score": 90, "met": True})).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value  # ran without pausing


async def test_approval_pauses_then_runs_on_approve(session: AsyncSession) -> None:
    from app.repositories.step import StepRepository as _Steps
    from app.services.task import TaskService

    plans = [
        {"thought": "run", "tool": "run_command", "args": {"command": "whoami"}},
        {"thought": "done", "tool": "finish", "args": {"summary": "ran it"}},
    ]
    task = await _make_task(session, max_steps=8, token_budget=1_000_000, require_approval=True)
    svc = _service(session, ScriptedLLM(plans, verify={"score": 90, "met": True}))

    await svc.run(task.id)
    await session.refresh(task)
    assert task.status == TaskStatus.AWAITING_INPUT.value
    assert task.pending_action is not None
    assert "whoami" in (task.pending_question or "")

    await TaskService(TaskRepository(session), _Steps(session)).respond(task.id, "yes")
    await session.refresh(task)
    assert task.pending_action is not None  # approved -> kept for the resumed run

    await svc.run(task.id)
    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    steps = await _Steps(session).list_for_task(task.id)
    assert any(s.tool == "run_command" and s.status == "ok" for s in steps)  # it ran


async def test_approval_denied_skips_the_command(session: AsyncSession) -> None:
    from app.repositories.step import StepRepository as _Steps
    from app.services.task import TaskService

    plans = [
        {"thought": "run", "tool": "run_command", "args": {"command": "whoami"}},
        {"thought": "other", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {"thought": "done", "tool": "finish", "args": {"summary": "did other work"}},
    ]
    task = await _make_task(session, max_steps=8, token_budget=1_000_000, require_approval=True)
    svc = _service(session, ScriptedLLM(plans, verify={"score": 90, "met": True}))

    await svc.run(task.id)  # pauses
    await TaskService(TaskRepository(session), _Steps(session)).respond(task.id, "no")
    await session.refresh(task)
    assert task.pending_action is None  # denied -> dropped

    await svc.run(task.id)  # resume; command skipped, agent does other work
    steps = await _Steps(session).list_for_task(task.id)
    assert not any(s.tool == "run_command" and s.status == "ok" for s in steps)  # never ran


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


async def test_spawn_delegates_to_a_verified_subagent(session: AsyncSession) -> None:
    # Parent delegates, the child writes a file and finishes, then the parent finishes.
    plans = [
        {
            "thought": "delegate",
            "tool": "spawn",
            "args": {"goal": "write child.txt with hello", "token_budget": 5000, "max_steps": 4},
        },
        {
            "thought": "child writes",
            "tool": "write_file",
            "args": {"path": "child.txt", "content": "hello"},
        },
        {"thought": "child done", "tool": "finish", "args": {"summary": "wrote child.txt"}},
        {"thought": "parent done", "tool": "finish", "args": {"summary": "delegated and composed"}},
    ]
    parent = await _make_task(session, max_steps=10, token_budget=1_000_000)
    llm = ScriptedLLM(plans)
    await _service(session, llm).run(parent.id)

    await session.refresh(parent)
    assert parent.status == TaskStatus.COMPLETED.value

    # A child task was created, linked to the parent, one level deeper, and done.
    repo = TaskRepository(session)
    children = [t for t in await repo.list(limit=50, offset=0) if t.parent_id == parent.id]
    assert len(children) == 1
    child = children[0]
    assert child.depth == 1 and parent.depth == 0
    assert child.status == TaskStatus.COMPLETED.value
    assert child.stop_reason == StopReason.GOAL_ACHIEVED.value

    # The child's output was composed back into the parent's workspace, and its
    # token cost was folded into the parent's budget.
    copied = Path(parent.workspace_path) / "subtasks" / str(child.id)[:8] / "child.txt"
    assert copied.read_text() == "hello"
    assert parent.tokens_used >= child.tokens_used > 0

    # The archive carries a conftest so the parent's pytest never double-collects a
    # grafted test file (which would break the parent's whole run with exit 2).
    conftest = Path(parent.workspace_path) / "subtasks" / "conftest.py"
    assert conftest.exists() and "collect_ignore_glob" in conftest.read_text()

    steps = await StepRepository(session).list_for_task(parent.id)
    spawn_step = next(s for s in steps if s.tool == "spawn")
    assert spawn_step.status == "ok"
    assert "Sub-agent" in spawn_step.observation


async def test_spawn_blocked_at_max_depth(session: AsyncSession) -> None:
    # A task already at the max depth cannot spawn further; it must do the work.
    plans = [
        {"thought": "try to delegate", "tool": "spawn", "args": {"goal": "do a sub thing"}},
        {"thought": "ok I'll finish", "tool": "finish", "args": {"summary": "done myself"}},
    ]
    task = await _make_task(
        session, max_steps=10, token_budget=1_000_000, depth=settings.agent_max_spawn_depth
    )
    await _service(session, ScriptedLLM(plans)).run(task.id)

    await session.refresh(task)
    repo = TaskRepository(session)
    children = [t for t in await repo.list(limit=50, offset=0) if t.parent_id == task.id]
    assert children == []  # nothing was spawned
    steps = await StepRepository(session).list_for_task(task.id)
    spawn_step = next(s for s in steps if s.tool == "spawn")
    assert spawn_step.status == "blocked"
    assert "depth limit" in spawn_step.observation


async def test_send_email_pauses_for_approval(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With email enabled + configured, the planner may call send_email — which
    # always pauses for a human yes/no before the message goes out.
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "me@example.com")
    monkeypatch.setattr(settings, "smtp_password", "pw")
    plans = [
        {
            "thought": "email them",
            "tool": "send_email",
            "args": {"to": "a@b.com", "subject": "Hi", "body": "yo"},
        }
    ]
    task = await TaskRepository(session).create(
        goal="email someone",
        status=TaskStatus.PENDING.value,
        rubric=[],
        use_email=True,
        max_steps=6,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()
    await _service(session, ScriptedLLM(plans)).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.AWAITING_INPUT.value
    assert task.pending_action["tool"] == "send_email"
    assert task.pending_action["args"]["to"] == "a@b.com"
    assert "approve" in (task.pending_question or "").lower()


async def test_create_event_pauses_for_approval(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Calendar enabled + configured -> create_event always pauses for approval.
    monkeypatch.setattr(settings, "caldav_url", "https://dav.example.com")
    monkeypatch.setattr(settings, "caldav_user", "me")
    monkeypatch.setattr(settings, "caldav_password", "pw")
    plans = [
        {
            "thought": "book it",
            "tool": "create_event",
            "args": {"summary": "Dentist", "start": "2026-07-02T15:00:00"},
        }
    ]
    task = await TaskRepository(session).create(
        goal="add a calendar event",
        status=TaskStatus.PENDING.value,
        rubric=[],
        use_calendar=True,
        max_steps=6,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()
    await _service(session, ScriptedLLM(plans)).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.AWAITING_INPUT.value
    assert task.pending_action["tool"] == "create_event"
    assert "approve" in (task.pending_question or "").lower()


async def test_demo_mode_runs_a_verified_task_with_no_api_key(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DEMO_MODE + the scripted "mock" model drives a real, re-execution-verified
    # task (write fib.py, run it, finish with checks) — no API key at all.
    from app.core.llm.client import FallbackLLMClient

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "agent_sandbox", "inline")  # host python3, deterministic

    task = await _make_task(session, max_steps=8, token_budget=1_000_000)
    service = AgentReactService(
        TaskRepository(session), StepRepository(session), FallbackLLMClient(primary="mock")
    )
    await service.run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.verified_by == "execution"  # the checks actually re-ran and passed
    assert task.receipt_hash  # a Receipt was produced
    assert (Path(task.workspace_path) / "fib.py").exists()


async def test_conversation_context_from_prior_turns(session: AsyncSession) -> None:
    repo = TaskRepository(session)
    common = dict(  # noqa: C408
        rubric=[],
        max_steps=5,
        token_budget=1000,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await repo.create(
        goal="write greet.py",
        status=TaskStatus.COMPLETED.value,
        chat_id="s1",
        summary="Wrote greet.py that prints hello.",
        **common,
    )
    await session.commit()
    current = await repo.create(
        goal="now add a docstring to it",
        status=TaskStatus.PENDING.value,
        chat_id="s1",
        summary=None,
        **common,
    )
    solo = await repo.create(
        goal="unrelated", status=TaskStatus.PENDING.value, chat_id=None, summary=None, **common
    )
    await session.commit()

    svc = _service(session, ScriptedLLM([]))
    convo = await svc._build_conversation(current)
    assert "write greet.py" in convo and "Wrote greet.py" in convo  # prior turn threaded in
    assert await svc._build_conversation(solo) == ""  # no chat_id -> no context


async def test_ledger_stays_valid_after_respond(session: AsyncSession) -> None:
    # Regression: recording a user answer used to rewrite the last step's
    # observation after its hash was set, breaking verify_chain for every
    # human-in-the-loop task. The chain must stay valid across respond().
    from app.services.task import TaskService

    plans = [{"thought": "need info", "tool": "ask_user", "args": {"question": "what color?"}}]
    task = await _make_task(session, max_steps=6, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans)).run(task.id)
    await session.refresh(task)
    assert task.status == TaskStatus.AWAITING_INPUT.value

    svc = TaskService(TaskRepository(session), StepRepository(session))
    assert (await svc.verify_ledger(task.id))["verified"] is True  # valid before answering
    await svc.respond(task.id, "blue")
    assert (await svc.verify_ledger(task.id))["verified"] is True  # still valid after (was broken)


async def test_rejected_finish_on_last_step_is_not_stuck_running(session: AsyncSession) -> None:
    # Regression: a finish rejected on the final allowed step used to `continue`
    # off the end of the loop, leaving the task RUNNING forever.
    plans = [{"thought": "done?", "tool": "finish", "args": {"summary": "maybe"}}]
    task = await _make_task(session, max_steps=1, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans, verify={"score": 10, "met": False})).run(task.id)
    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value  # reached a terminal state
    assert task.status != TaskStatus.RUNNING.value


async def test_spawn_refused_when_budget_too_low(session: AsyncSession) -> None:
    # Safety: flooring a child at 1000 when the parent has less left would let the
    # sub-tree overshoot the global token ceiling. Refuse instead — no child made.
    plans = [{"thought": "delegate", "tool": "spawn", "args": {"goal": "do a big subtask"}}]
    task = await _make_task(session, max_steps=1, token_budget=500)
    await _service(session, ScriptedLLM(plans, understand_tokens=0, plan_tokens=50)).run(task.id)

    steps = await StepRepository(session).list_for_task(task.id)
    spawn_steps = [s for s in steps if s.tool == "spawn"]
    assert spawn_steps and spawn_steps[0].status == "blocked"
    assert "not enough" in spawn_steps[0].observation.lower()
    assert await TaskRepository(session).list_children(task.id) == []  # ceiling preserved


async def test_secrets_redacted_in_recorded_step(session: AsyncSession) -> None:
    # A command that surfaces a credential must not seal it into the ledger/history.
    secret = "sk-abcdefghij1234567890KLMN"
    cmd = f"echo DEEPSEEK_API_KEY={secret}"
    plans = [{"thought": "x", "tool": "run_command", "args": {"command": cmd}}]
    task = await _make_task(session, max_steps=1, token_budget=1_000_000)
    await _service(session, ScriptedLLM(plans)).run(task.id)

    steps = await StepRepository(session).list_for_task(task.id)
    step = next(s for s in steps if s.tool == "run_command")
    assert secret not in step.observation and "[REDACTED]" in step.observation


async def test_ledger_valid_after_approval_respond(session: AsyncSession) -> None:
    # The ledger re-seal must also hold on the approval branch of respond()
    # (a non-allowlisted command pauses for approval, then is approved).
    from app.services.task import TaskService

    plans = [{"thought": "run it", "tool": "run_command", "args": {"command": "chmod 644 x.txt"}}]
    task = await _make_task(session, max_steps=6, token_budget=1_000_000, require_approval=True)
    await _service(session, ScriptedLLM(plans)).run(task.id)
    await session.refresh(task)
    assert task.status == TaskStatus.AWAITING_INPUT.value  # paused for approval
    assert task.pending_action is not None

    svc = TaskService(TaskRepository(session), StepRepository(session))
    assert (await svc.verify_ledger(task.id))["verified"] is True  # valid while paused
    await svc.respond(task.id, "yes")  # approve -> observation edited + re-sealed
    assert (await svc.verify_ledger(task.id))["verified"] is True  # still valid


async def test_understand_failure_is_not_fatal(session: AsyncSession) -> None:
    # A transient blip on the first (understand) call must not kill the task at
    # 0 steps — fall back to a generic rubric and let the plan phase run.
    from app.core.llm import LLMError

    class _UnderstandFails(ScriptedLLM):
        async def complete(self, system: str, user: str, **kw: object) -> object:
            if "JSON array of 3 to 6" in user:  # the understand call
                raise LLMError("transient understand failure", retryable=True)
            return await super().complete(system, user, **kw)  # type: ignore[arg-type]

    plans = [{"thought": "done", "tool": "finish", "args": {"summary": "ok"}}]
    task = await _make_task(session, max_steps=3, token_budget=1_000_000)
    await _service(session, _UnderstandFails(plans, verify={"score": 90, "met": True})).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value  # proceeded despite understand failing
    assert task.rubric == ["Fully and correctly satisfies the task"]  # default fallback


async def test_unparseable_plan_output_is_handled_not_crashing(session: AsyncSession) -> None:
    # A model that returns prose instead of JSON must not crash the loop — it gets
    # an error observation and the run terminates (stuck), still with a Receipt.
    class _GarbageLLM(ScriptedLLM):
        async def complete(self, system: str, user: str, **kw: object) -> LLMResult:
            if "JSON array of 3 to 6" in user:  # understand
                return LLMResult('["produce a result"]', "fake", 10)
            if '"met"' in user:  # verify
                return LLMResult(json.dumps({"score": 10, "met": False}), "fake", 20)
            return LLMResult("sorry, I cannot help with that — just prose", "fake", 100)  # plan

    task = await _make_task(session, max_steps=12, token_budget=1_000_000)
    await _service(session, _GarbageLLM([])).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value  # terminated cleanly, no crash
    assert task.stop_reason in {StopReason.STUCK.value, StopReason.MAX_STEPS.value}
    steps = await StepRepository(session).list_for_task(task.id)
    assert any("parse a valid action" in s.observation for s in steps)
    # A non-accepted stop still gets a plain-language summary (not a bare score-0 row).
    assert task.summary and "Stopped" in task.summary
    # ...and a tamper-evident Receipt marked "unverified", so a failure is auditable too.
    assert task.receipt_hash
    receipt = json.loads(Workspace(Path(task.workspace_path)).read("receipt.json"))
    assert receipt["verified_by"] == "unverified"
    from app.services.receipt import verify_receipt

    ok, _ = verify_receipt(receipt)
    assert ok  # the unverified receipt's own content hash checks out


async def test_crash_mid_run_fails_cleanly_with_unverified_receipt(session: AsyncSession) -> None:
    # An unexpected exception must fail the task (not strand it RUNNING) AND still
    # leave an auditable Receipt of whatever happened before the crash.
    class _BoomLLM(ScriptedLLM):
        async def complete(self, system: str, user: str, **kw: object) -> LLMResult:
            raise RuntimeError("boom in the model call")

    task = await _make_task(session, max_steps=8, token_budget=1_000_000)
    await _service(session, _BoomLLM([])).run(task.id)

    await session.refresh(task)
    assert task.status == TaskStatus.FAILED.value
    assert task.stop_reason == StopReason.ERROR.value
    assert "boom" in (task.error or "")
    assert task.receipt_hash  # a crash is auditable too
    receipt = json.loads(Workspace(Path(task.workspace_path)).read("receipt.json"))
    assert receipt["verified_by"] == "unverified"


async def test_spawned_child_cost_folds_into_parent_budget(session: AsyncSession) -> None:
    # The child's tokens count against the parent's ceiling — this is what keeps a
    # spawn tree bounded by the parent's budget rather than multiplying it.
    class _SpawnLLM(ScriptedLLM):
        async def complete(self, system: str, user: str, **kw: object) -> LLMResult:
            if "JSON array of 3 to 6" in user:  # understand (parent + child)
                return LLMResult('["produce a result"]', "fake", 10)
            if '"met"' in user:  # verify -> accept
                return LLMResult(json.dumps({"score": 95, "met": True, "missing": []}), "fake", 20)
            if "Sub-agent for" in user:  # parent, AFTER the spawn -> finish
                return LLMResult(
                    json.dumps(
                        {"thought": "compose", "tool": "finish", "args": {"summary": "done"}}
                    ),
                    "fake",
                    30,
                )
            if "the sub-thing" in user:  # the child's own plan -> finish immediately
                return LLMResult(
                    json.dumps(
                        {"thought": "child done", "tool": "finish", "args": {"summary": "sub done"}}
                    ),
                    "fake",
                    500,
                )
            return LLMResult(  # parent, first plan -> delegate
                json.dumps(
                    {"thought": "delegate", "tool": "spawn", "args": {"goal": "do the sub-thing"}}
                ),
                "fake",
                100,
            )

    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    await _service(session, _SpawnLLM([])).run(task.id)

    children = await TaskRepository(session).list_children(task.id)
    assert len(children) == 1
    child = children[0]
    assert child.tokens_used > 0
    await session.refresh(task)
    assert task.tokens_used >= child.tokens_used  # child cost folded into the parent's ceiling


async def test_nonsubstantiating_checks_degrade_execution_to_judgment(
    session: AsyncSession,
) -> None:
    # The agent attaches a trivial passing check (echo hi). The verifier flags it as
    # not substantiating the goal, so the run is accepted but labelled judgment, not
    # execution — a tautological check can't earn the stronger proof label.
    plans = [
        {"thought": "w", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {
            "thought": "done",
            "tool": "finish",
            "args": {
                "summary": "did it",
                "checks": [{"kind": "command", "command": "echo hi", "expect_exit": 0}],
            },
        },
    ]
    llm = ScriptedLLM(plans, verify={"score": 90, "met": True, "checks_substantiate": False})
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value  # still accepted
    assert task.verified_by == "judgment"  # ...but NOT execution — the check was trivial
    receipt = json.loads(Workspace(Path(task.workspace_path)).read("receipt.json"))
    assert receipt["coverage"]["execution_backed"] is False
    assert receipt["coverage"]["checks"] == 1


async def test_email_activation_warns_it_is_out_of_sandbox(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Email reaches the network outside the container; the planner must be told so
    # explicitly (it's an out-of-sandbox path), not silently.
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(_settings, "smtp_user", "me@example.com")
    monkeypatch.setattr(_settings, "smtp_password", "pw")
    task = await _make_task(session, max_steps=2, token_budget=1_000_000)
    task.use_email = True
    await session.commit()

    svc = _service(session, ScriptedLLM([{"tool": "finish", "args": {"summary": "x"}}]))
    await svc.run(task.id)
    assert "OUTSIDE the container sandbox" in svc._notices


async def test_checks_substantiate_string_false_degrades_to_judgment(
    session: AsyncSession,
) -> None:
    # A stringified "false" from the verifier must NOT slip through bool() as truthy;
    # the run degrades to judgment just like a real False (fail-open bug fixed).
    plans = [
        {"thought": "w", "tool": "write_file", "args": {"path": "a.txt", "content": "x"}},
        {
            "thought": "done",
            "tool": "finish",
            "args": {
                "summary": "did it",
                "checks": [{"kind": "command", "command": "echo hi", "expect_exit": 0}],
            },
        },
    ]
    llm = ScriptedLLM(plans, verify={"score": 90, "met": True, "checks_substantiate": "false"})
    task = await _make_task(session, max_steps=10, token_budget=1_000_000)
    await _service(session, llm).run(task.id)

    await session.refresh(task)
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.verified_by == "judgment"
