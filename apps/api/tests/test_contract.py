from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import LLMResult
from app.domain.capability import Capability
from app.domain.task import StopReason, TaskStatus
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate
from app.services.agent_react import AgentReactService
from app.services.contract import (
    compile_project_contract,
    discover_repository,
    lock_user_project_contract,
    verify_contract_hash,
)
from app.services.task import TaskService


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path) -> Path:
    repo = root / "project"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "loop@example.com")
    _git(repo, "config", "user.name", "Loop Test")
    (repo / "app.py").write_text("print('before')\n")
    (repo / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.1.0'\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_placeholder():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "initial")
    return repo


class ContractLoopLLM:
    def __init__(
        self,
        *,
        critic_accepts: bool = True,
        network_check: bool = False,
        risk: str = "low",
        confidence: int = 96,
    ) -> None:
        self.critic_accepts = critic_accepts
        self.network_check = network_check
        self.risk = risk
        self.confidence = confidence
        self.plan_index = 0

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        token_budget: int | None = None,
    ) -> LLMResult:
        del max_tokens, temperature, token_budget
        if "compile one software instruction" in system:
            return LLMResult(
                json.dumps(
                    {
                        "criteria": [
                            "app.py prints after instead of before",
                            "Running app.py exits successfully and reports after",
                        ],
                        "checks": [
                            {
                                "kind": "file_contains",
                                "path": "app.py",
                                "text": "print('after')",
                                "criterion_ids": ["criterion-001"],
                            },
                            {
                                "kind": "command",
                                "command": (
                                    "curl https://example.com"
                                    if self.network_check
                                    else "python3 app.py"
                                ),
                                "expect_stdout": "after",
                                "criterion_ids": ["criterion-002"],
                            },
                        ],
                        "artifacts": ["app.py"],
                        "risk": self.risk,
                        "assumptions": ["The printed word is the requested behavior."],
                        "confidence": self.confidence,
                        "authority_requests": [],
                    }
                ),
                "fixture",
                20,
                model="contract-v1",
            )
        if "independent acceptance-contract critic" in system:
            return LLMResult(
                json.dumps(
                    {
                        "accepted": self.critic_accepts,
                        "issues": (
                            [] if self.critic_accepts else ["The intended word is ambiguous."]
                        ),
                        "question": (
                            None if self.critic_accepts else "Which word should app.py print?"
                        ),
                    }
                ),
                "fixture-critic",
                10,
                model="critic-v1",
            )
        if "demanding verifier" in system:
            return LLMResult(
                json.dumps(
                    {
                        "score": 98,
                        "met": True,
                        "checks_substantiate": True,
                        "missing": [],
                    }
                ),
                "fixture-critic",
                10,
                model="critic-v1",
            )
        plans = [
            {
                "thought": "Try the first edit.",
                "tool": "edit_file",
                "args": {"path": "app.py", "old": "before", "new": "wrong"},
            },
            {
                "thought": "Run the current result and use the failure as evidence.",
                "tool": "run_command",
                "args": {"command": "python3 app.py"},
            },
            {
                "thought": "Repair the observed mismatch.",
                "tool": "edit_file",
                "args": {"path": "app.py", "old": "wrong", "new": "after"},
            },
        ]
        decision = plans[min(self.plan_index, len(plans) - 1)]
        self.plan_index += 1
        return LLMResult(json.dumps(decision), "fixture", 10, model="executor-v1")


@pytest.fixture
def project_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    projects = tmp_path / "projects"
    projects.mkdir()
    source = _repository(projects)
    monkeypatch.setattr(settings, "loop_local_projects_root", str(projects))
    monkeypatch.setattr(settings, "agent_workspaces_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(settings, "agent_memory_root", str(tmp_path / "memory"))
    monkeypatch.setattr(settings, "agent_sandbox", "inline")
    return source


def test_repository_discovery_is_bounded_and_read_only(project_settings: Path) -> None:
    before = _git(project_settings, "status", "--porcelain")
    discovery = discover_repository(project_settings)

    assert discovery.manifests == ["pyproject.toml"]
    assert discovery.test_files == ["tests/test_app.py"]
    assert discovery.files_scanned == 3
    assert discovery.quality_checks[0].command == "pytest -q"
    assert _git(project_settings, "status", "--porcelain") == before == ""


async def test_compiler_cannot_smuggle_network_authority(project_settings: Path) -> None:
    model = ContractLoopLLM(network_check=True)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash is None
    assert compiled.draft.critique.accepted is False
    assert any("denied shell network access" in issue for issue in compiled.draft.critique.issues)


@pytest.mark.parametrize(
    ("risk", "confidence", "issue_fragment"),
    [("medium", 96, "only low-risk"), ("low", 79, "requires at least 80%")],
)
async def test_generated_contract_only_auto_starts_when_safe_and_confident(
    project_settings: Path,
    risk: str,
    confidence: int,
    issue_fragment: str,
) -> None:
    model = ContractLoopLLM(risk=risk, confidence=confidence)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash is None
    assert any(issue_fragment in issue for issue in compiled.draft.critique.issues)


async def test_compiler_preserves_explicit_advanced_checks(project_settings: Path) -> None:
    model = ContractLoopLLM()
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        required_checks=[
            {
                "id": "ignored",
                "kind": "file_exists",
                "path": "user-required.txt",
                "source": "contract",
            }
        ],
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert "user-required.txt" in compiled.draft.artifacts
    assert any(check.path == "user-required.txt" for check in compiled.draft.checks)


def test_user_contract_supports_full_advanced_input_bounds(project_settings: Path) -> None:
    required_checks = [
        {
            "id": f"command-{index}",
            "kind": "command",
            "command": "python3 app.py",
            "source": "contract",
        }
        for index in range(8)
    ]
    required_checks.extend(
        {
            "id": f"artifact-{index}",
            "kind": "file_exists",
            "path": f"artifact-{index}.txt",
            "source": "contract",
        }
        for index in range(32)
    )

    draft, contract_hash = lock_user_project_contract(
        root=project_settings,
        criteria=["The requested output exists and is validated."],
        required_checks=required_checks,
    )

    assert len(draft.artifacts) == 32
    assert len(draft.checks) == 41
    assert verify_contract_hash(draft, contract_hash)


async def test_one_instruction_compiles_repairs_verifies_and_applies(
    session: AsyncSession, project_settings: Path
) -> None:
    tasks = TaskRepository(session)
    steps = StepRepository(session)
    task_service = TaskService(tasks, steps)
    task = await task_service.publish(
        TaskCreate(
            goal="Change app.py so it prints after and verify the result",
            project_path="project",
            autostart=False,
        )
    )
    assert task.rubric == []
    assert task.contract_status == "pending"
    assert task.requested_capabilities == ["exec", "fs.read", "fs.write"]

    model = ContractLoopLLM()
    await AgentReactService(tasks, steps, model, verifier_llm=model).run(task.id)
    await session.refresh(task)

    assert task.status == TaskStatus.COMPLETED.value
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.criteria_source == "compiled"
    assert task.contract_status == "locked"
    assert task.contract_hash
    assert verify_contract_hash(task.contract_draft, task.contract_hash)
    assert task.contract_draft["compiler"] == {
        "provider": "fixture",
        "model": "contract-v1",
    }
    assert task.executor_models == [{"provider": "fixture", "model": "executor-v1"}]
    tampered = json.loads(json.dumps(task.contract_draft))
    tampered["criteria"][0] = "A weaker criterion"
    assert not verify_contract_hash(tampered, task.contract_hash)
    assert task.verified_by == "execution"
    assert task.verification_score == 98
    assert task.steps_used == 4
    assert any(check["source"] == "system" for check in task.required_checks)
    assert any(
        check["source"] == "contract"
        and check["kind"] == "file_exists"
        and check["path"] == "app.py"
        for check in task.required_checks
    )
    assert Path(task.workspace_path or "", "app.py").read_text() == "print('after')\n"  # noqa: ASYNC240
    receipt = json.loads(
        Path(task.workspace_path or "", "receipt.json").read_text()  # noqa: ASYNC240
    )
    assert receipt["contract"]["hash"] == task.contract_hash
    assert receipt["contract"]["draft"]["critique"]["accepted"] is True

    applied = await task_service.apply_change_set(task.id)
    assert applied.state == "applied"
    assert (project_settings / "app.py").read_text() == "print('after')\n"
    undone = await task_service.undo_change_set(task.id)
    assert undone.state == "reverted"
    assert (project_settings / "app.py").read_text() == "print('before')\n"


async def test_rejected_contract_pauses_before_mutation(
    session: AsyncSession, project_settings: Path
) -> None:
    tasks = TaskRepository(session)
    steps = StepRepository(session)
    task = await TaskService(tasks, steps).publish(
        TaskCreate(
            goal="Change the printed value",
            project_path="project",
            autostart=False,
        )
    )
    model = ContractLoopLLM(critic_accepts=False)
    await AgentReactService(tasks, steps, model, verifier_llm=model).run(task.id)
    await session.refresh(task)

    assert task.status == TaskStatus.AWAITING_INPUT.value
    assert task.contract_status == "awaiting_input"
    assert task.contract_hash is None
    assert task.pending_question == "Which word should app.py print?"
    assert task.steps_used == 0
    assert Path(task.workspace_path or "", "app.py").read_text() == "print('before')\n"  # noqa: ASYNC240
    assert (project_settings / "app.py").read_text() == "print('before')\n"

    task_service = TaskService(tasks, steps)
    resumed = await task_service.respond(task.id, "It must print after.")
    assert resumed.status == TaskStatus.PENDING.value
    assert resumed.contract_status == "pending"
    assert resumed.contract_draft["clarifications"] == ["It must print after."]

    model.critic_accepts = True
    await AgentReactService(tasks, steps, model, verifier_llm=model).run(task.id)
    await session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.contract_status == "locked"
    assert task.contract_draft["clarifications"] == ["It must print after."]
