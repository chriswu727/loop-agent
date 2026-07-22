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
from app.schemas.contract import ContractProposal
from app.schemas.task import TaskCreate
from app.services.agent_react import AgentReactService
from app.services.contract import (
    _deterministic_issues,
    _effective_checks,
    _inline_python_syntax_issue,
    _test_previews_cover_contract,
    compile_project_contract,
    discover_repository,
    lock_user_project_contract,
    verify_contract_hash,
)
from app.services.prompts import contract_repair_prompts
from app.services.task import TaskService

ROOT = Path(__file__).parents[3]


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
        omit_check_kinds: bool = False,
        critic_rejects_once: bool = False,
        criteria_as_objects: bool = False,
        include_discovered_check: bool = False,
        null_expect_exit: bool = False,
        critic_question: str | None = "Which word should app.py print?",
        critic_issues: list[str] | None = None,
        assumptions_as_string: bool = False,
    ) -> None:
        self.critic_accepts = critic_accepts
        self.network_check = network_check
        self.risk = risk
        self.confidence = confidence
        self.omit_check_kinds = omit_check_kinds
        self.critic_rejects_once = critic_rejects_once
        self.criteria_as_objects = criteria_as_objects
        self.include_discovered_check = include_discovered_check
        self.null_expect_exit = null_expect_exit
        self.critic_question = critic_question
        self.critic_issues = critic_issues or ["The intended word is ambiguous."]
        self.assumptions_as_string = assumptions_as_string
        self.plan_index = 0
        self.compile_calls = 0
        self.critic_calls = 0
        self.critic_prompt = ""
        self.critic_system = ""
        self.compile_prompt = ""
        self.compile_system = ""
        self.repair_prompt = ""

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
            self.compile_calls += 1
            self.compile_system = system
            self.compile_prompt = user
            if "Repair the rejected draft" in system:
                self.repair_prompt = system
            return LLMResult(
                json.dumps(
                    {
                        "criteria": (
                            [
                                {
                                    "id": "criterion-001",
                                    "description": (
                                        "criterion-001: app.py prints after instead of before"
                                    ),
                                },
                                {
                                    "id": "criterion-002",
                                    "description": (
                                        "criterion-002: Running app.py exits successfully and "
                                        "reports after"
                                    ),
                                },
                            ]
                            if self.criteria_as_objects
                            else [
                                "app.py prints after instead of before",
                                "Running app.py exits successfully and reports after",
                            ]
                        ),
                        "checks": [
                            {
                                **({} if self.omit_check_kinds else {"kind": "file_contains"}),
                                "path": "app.py",
                                "text": "print('after')",
                                **({"expect_exit": None} if self.null_expect_exit else {}),
                                "criterion_ids": ["criterion-001"],
                            },
                            {
                                **({} if self.omit_check_kinds else {"kind": "command"}),
                                "command": (
                                    "curl https://example.com"
                                    if self.network_check
                                    else "python3 app.py"
                                ),
                                "expect_stdout": "after",
                                **({"expect_exit": None} if self.null_expect_exit else {}),
                                "criterion_ids": ["criterion-002"],
                            },
                            *(
                                [
                                    {
                                        "kind": "command",
                                        "command": "python3 -m pytest -q",
                                        "criterion_ids": ["criterion-002"],
                                    }
                                ]
                                if self.include_discovered_check
                                else []
                            ),
                        ],
                        "artifacts": ["app.py"],
                        "risk": self.risk,
                        "assumptions": (
                            "The printed word is the requested behavior."
                            if self.assumptions_as_string
                            else ["The printed word is the requested behavior."]
                        ),
                        "confidence": self.confidence,
                        "authority_requests": [],
                    }
                ),
                "fixture",
                20,
                model="contract-v1",
            )
        if "independent acceptance-contract critic" in system:
            self.critic_calls += 1
            self.critic_system = system
            self.critic_prompt = user
            critic_accepts = self.critic_accepts and not (
                self.critic_rejects_once and self.critic_calls == 1
            )
            return LLMResult(
                json.dumps(
                    {
                        "accepted": critic_accepts,
                        "issues": ([] if critic_accepts else self.critic_issues),
                        "question": (None if critic_accepts else self.critic_question),
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
    assert discovery.quality_checks[0].command == "python3 -m pytest -q"
    assert "print('before')" in discovery.file_previews["app.py"]
    assert "test_placeholder" in discovery.file_previews["tests/test_app.py"]
    assert _git(project_settings, "status", "--porcelain") == before == ""


def test_repository_discovery_does_not_preview_secret_named_files(
    project_settings: Path,
) -> None:
    (project_settings / ".env").write_text("DEEPSEEK_API_KEY=not-a-real-key\n")
    (project_settings / "private-credentials.json").write_text('{"token":"hidden"}\n')

    discovery = discover_repository(project_settings)

    assert ".env" not in discovery.file_previews
    assert "private-credentials.json" not in discovery.file_previews


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


async def test_compiler_infers_unambiguous_missing_check_kinds(
    project_settings: Path,
) -> None:
    model = ContractLoopLLM(omit_check_kinds=True)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert [check.kind for check in compiled.draft.checks if check.source == "contract"] == [
        "file_contains",
        "command",
        "file_exists",
    ]


async def test_compiler_normalizes_null_check_defaults(project_settings: Path) -> None:
    model = ContractLoopLLM(null_expect_exit=True)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert all(check.expect_exit == 0 for check in compiled.draft.checks)


async def test_compiler_normalizes_structured_criteria(project_settings: Path) -> None:
    model = ContractLoopLLM(criteria_as_objects=True)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert compiled.draft.criteria == [
        "app.py prints after instead of before",
        "Running app.py exits successfully and reports after",
    ]


async def test_discovered_checks_are_not_duplicated(project_settings: Path) -> None:
    model = ContractLoopLLM(include_discovered_check=True)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    pytest_checks = [
        check for check in compiled.draft.checks if check.command == "python3 -m pytest -q"
    ]
    assert len(pytest_checks) == 1
    assert pytest_checks[0].source == "contract"
    assert pytest_checks[0].criterion_ids == ["criterion-001", "criterion-002"]
    assert all(check.kind != "file_contains" for check in compiled.draft.checks)
    assert all(check.kind != "file_exists" for check in compiled.draft.checks)


async def test_critic_reviews_discovered_quality_checks(project_settings: Path) -> None:
    model = ContractLoopLLM()
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert '"command": "python3 -m pytest -q"' in model.critic_prompt
    assert '"source": "system"' in model.critic_prompt
    assert "post-change acceptance checks" in model.critic_system
    assert "direct behavioral evidence" in model.critic_system
    assert "typed field, key suffix" in model.critic_system
    assert "ordinary strings accepted in other fields" in model.compile_system
    repair_system, _ = contract_repair_prompts(
        "Update the local greeting",
        ContractProposal.model_validate(
            {
                "criteria": ["app.py prints after"],
                "checks": [],
                "artifacts": ["app.py"],
                "risk": "low",
                "assumptions": [],
                "confidence": 95,
                "authority_requests": [],
            }
        ),
        discover_repository(project_settings),
        ["An unrequested existing behavior is not covered."],
    )
    assert "remove the invented criterion" in repair_system
    assert "'nonzero'" in model.compile_prompt


async def test_deterministic_contract_adjudicates_non_actionable_critic_noise(
    project_settings: Path,
) -> None:
    model = ContractLoopLLM(
        critic_accepts=False,
        critic_question="Should Loop simplify the contract and remove the redundant checks?",
        critic_issues=["The file check is redundant with the repository test suite."],
        include_discovered_check=True,
    )
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert compiled.draft.critique.accepted is True
    assert compiled.draft.critique.adjudicated is True
    assert compiled.draft.critique.adjudication_reason
    assert compiled.draft.critique.issues == [
        "The file check is redundant with the repository test suite."
    ]


async def test_deterministic_contract_does_not_overrule_a_coverage_warning(
    project_settings: Path,
) -> None:
    model = ContractLoopLLM(
        critic_accepts=False,
        critic_question=None,
        critic_issues=["The tests do not cover invalid input behavior."],
        include_discovered_check=True,
    )
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
    assert compiled.draft.critique.adjudicated is False


async def test_deterministic_contract_uses_test_preview_to_refute_check_isolation_noise(
    project_settings: Path,
) -> None:
    (project_settings / "tests" / "test_app.py").write_text(
        "def test_error():\n"
        "    result = type('Result', (), {'stderr': 'format error'})()\n"
        "    assert 'format' in result.stderr\n"
    )
    model = ContractLoopLLM(
        critic_accepts=False,
        critic_question=(
            "The acceptance contract is not yet verifiable. What observable behavior must be true?"
        ),
        critic_issues=[
            "The direct check does not require stderr to contain 'format', even though the "
            "criterion requires a 'format' error."
        ],
        include_discovered_check=True,
    )
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert compiled.draft.critique.adjudicated is True


async def test_deterministic_contract_refutes_test_runner_speculation(
    project_settings: Path,
) -> None:
    model = ContractLoopLLM(
        critic_accepts=False,
        critic_question=(
            "The acceptance contract is not yet verifiable. What observable behavior must be true?"
        ),
        critic_issues=[
            "The tests use unittest and the check runs pytest. The command does not specify "
            "the test file path, so the working directory may prevent the intended tests from "
            "running."
        ],
        include_discovered_check=True,
    )
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert compiled.draft.critique.adjudicated is True


def test_inline_python_contract_check_syntax_is_validated() -> None:
    invalid = 'python3 -c "value = 1; try: print(value); except ValueError: print(0)"'
    valid = 'python3 -c "value = 1\ntry:\n print(value)\nexcept ValueError:\n print(0)"'

    assert "syntax error" in (_inline_python_syntax_issue(invalid) or "")
    assert _inline_python_syntax_issue(valid) is None


def test_deterministic_contract_rejects_unforwarded_and_unasserted_output(
    project_settings: Path,
) -> None:
    proposal = ContractProposal.model_validate(
        {
            "criteria": [
                "The command prints JSON to stdout.",
                "Invalid format prints an error containing 'format' to stderr.",
            ],
            "checks": [
                {
                    "id": "contract-001",
                    "kind": "command",
                    "command": (
                        "node -e \"const {spawnSync}=require('child_process'); "
                        "const r=spawnSync('node',['app.js']); process.exit(r.status);\""
                    ),
                    "expect_stdout": "[]",
                    "criterion_ids": ["criterion-001"],
                    "source": "contract",
                },
                {
                    "id": "contract-002",
                    "kind": "command",
                    "command": "node app.js --format yaml",
                    "expect_exit": "nonzero",
                    "criterion_ids": ["criterion-002"],
                    "source": "contract",
                },
            ],
            "artifacts": ["app.js"],
            "risk": "low",
            "assumptions": [],
            "confidence": 95,
            "authority_requests": [],
        }
    )

    issues = _deterministic_issues(
        proposal,
        discover_repository(project_settings),
        {Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
    )

    assert any("never forwards it" in issue for issue in issues)
    assert any("stderr content" in issue for issue in issues)


def test_test_previews_canonicalize_redundant_cli_wrappers() -> None:
    discovery = discover_repository(ROOT / "evals" / "repositories" / "extend-task-cli")
    criteria = [
        "`list --status open --format json` exits 0 and prints a JSON array of open tasks.",
        "`list` still prints the tab-separated text format for all tasks.",
        "`list --format yaml` exits non-zero and prints an error containing 'format' to stderr.",
        "`list --status` exits non-zero and prints an error containing 'status' to stderr.",
    ]
    proposal = ContractProposal.model_validate(
        {
            "criteria": criteria,
            "checks": [
                {
                    "id": "contract-001",
                    "kind": "command",
                    "command": "node src/cli.mjs list --status open --format json",
                    "expect_stdout": "[]",
                    "criterion_ids": ["criterion-001"],
                    "source": "contract",
                },
                {
                    "id": "contract-002",
                    "kind": "file_exists",
                    "path": "src/cli.mjs",
                    "criterion_ids": [],
                    "source": "contract",
                },
            ],
            "artifacts": ["src/cli.mjs"],
            "risk": "low",
            "assumptions": [],
            "confidence": 95,
            "authority_requests": [],
        }
    )

    assert _test_previews_cover_contract(proposal, discovery) is True
    checks = _effective_checks(proposal, discovery)

    assert len(checks) == 1
    assert checks[0].command == "npm run test"
    assert checks[0].source == "contract"
    assert checks[0].criterion_ids == [
        "criterion-001",
        "criterion-002",
        "criterion-003",
        "criterion-004",
    ]


async def test_contract_compiler_normalizes_a_single_assumption_string(
    project_settings: Path,
) -> None:
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=ContractLoopLLM(assumptions_as_string=True),
        critic=ContractLoopLLM(),
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert compiled.draft.assumptions == ["The printed word is the requested behavior."]


async def test_compiler_runs_one_bounded_repair_after_critic_rejection(
    project_settings: Path,
) -> None:
    model = ContractLoopLLM(critic_rejects_once=True)
    compiled = await compile_project_contract(
        goal="Update the local greeting",
        root=project_settings,
        compiler=model,
        critic=model,
        granted_capabilities={Capability.FS_READ, Capability.FS_WRITE, Capability.EXEC},
        token_budget=10_000,
    )

    assert compiled.contract_hash
    assert compiled.draft.critique.accepted is True
    assert model.compile_calls == 2
    assert model.critic_calls == 2
    assert compiled.tokens_spent == 60
    assert "remove invented implementation requirements" in model.repair_prompt


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
    assert model.compile_calls == 2
    assert model.critic_calls == 2
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
