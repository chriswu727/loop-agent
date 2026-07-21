"""Read-only repository discovery and pre-mutation contract compilation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.llm import LLMClient, LLMError, LLMResult
from app.domain.capability import Capability
from app.schemas.contract import (
    ContractCheck,
    ContractCritique,
    ContractDraft,
    ContractModelIdentity,
    ContractProposal,
    RepositoryDiscovery,
)
from app.services.completion import discover_project_checks
from app.services.prompts import (
    contract_compile_prompts,
    contract_critic_prompts,
    contract_repair_prompts,
)
from app.tools.policy import Verdict, evaluate_command, network_command_reason
from app.tools.workspace import Workspace

_MANIFEST_NAMES = frozenset(
    {
        "Cargo.toml",
        "Makefile",
        "go.mod",
        "package.json",
        "pnpm-workspace.yaml",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
    }
)
_BUILD_DIRS = frozenset({".next", "build", "coverage", "dist", "out", "target"})
_TAUTOLOGIES = (
    re.compile(r"^fully( and correctly)? satisfies? the task[.!]?$", re.I),
    re.compile(r"^(the )?(requested )?(change|task|work) is (complete|correct|done)[.!]?$", re.I),
    re.compile(r"^produce(s)? a correct result[.!]?$", re.I),
    re.compile(r"^works? correctly[.!]?$", re.I),
)
_MIN_AUTO_CONFIDENCE = 80
_MAX_AUTO_CRITERIA = 8
_MAX_CONTRACT_ATTEMPTS = 3


@dataclass(frozen=True)
class CompiledContract:
    draft: ContractDraft
    contract_hash: str | None
    compiler_result: LLMResult
    critic_result: LLMResult | None
    tokens_spent: int


def lock_user_project_contract(
    *,
    root: Path,
    criteria: list[str],
    required_checks: list[dict[str, Any]],
) -> tuple[ContractDraft, str]:
    discovery = discover_repository(root)
    criterion_ids = [f"criterion-{index:03d}" for index in range(1, len(criteria) + 1)]
    checks: list[ContractCheck] = []
    for index, raw in enumerate(required_checks, start=1):
        check = dict(raw)
        check["id"] = str(check.get("id") or f"contract-{index:03d}")
        check["source"] = "contract"
        check["criterion_ids"] = list(check.get("criterion_ids") or criterion_ids)
        checks.append(ContractCheck.model_validate(check))
    checks.extend(discovery.quality_checks)
    artifacts = [
        check.path
        for check in checks
        if check.source == "contract" and check.kind == "file_exists" and check.path
    ]
    draft = ContractDraft(
        criteria=criteria,
        checks=checks,
        artifacts=artifacts,
        risk="low",
        assumptions=[],
        confidence=100,
        authority_requests=[],
        discovery=discovery,
        clarifications=[],
        critique=ContractCritique(
            accepted=True,
            issues=[],
            provider="user",
            model="user-confirmed",
        ),
        compiler=ContractModelIdentity(provider="user", model="user-confirmed"),
    )
    return draft, hash_contract(draft)


def discover_repository(root: Path) -> RepositoryDiscovery:
    workspace = Workspace(root)
    files = workspace.list_files(max_entries=501)
    truncated = len(files) > 500
    paths = [path for path, _ in files[:500]]
    manifests = [path for path in paths if Path(path).name in _MANIFEST_NAMES][:100]
    test_files = [path for path in paths if _is_test_path(path)][:100]
    build_outputs = sorted(
        entry.name for entry in root.iterdir() if entry.is_dir() and entry.name in _BUILD_DIRS
    )[:50]
    scripts: dict[str, str] = {}
    package = root / "package.json"
    if package.is_file():
        try:
            raw_scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError, AttributeError):
            raw_scripts = {}
        if isinstance(raw_scripts, dict):
            scripts = {
                str(name)[:100]: str(command)[:1_000]
                for name, command in sorted(raw_scripts.items())[:100]
                if isinstance(command, str)
            }
    quality_checks = [
        ContractCheck.model_validate(check) for check in discover_project_checks(root)
    ]
    return RepositoryDiscovery(
        manifests=manifests,
        scripts=scripts,
        test_files=test_files,
        build_outputs=build_outputs,
        quality_checks=quality_checks,
        files_scanned=min(len(files), 500),
        truncated=truncated,
    )


async def compile_project_contract(
    *,
    goal: str,
    root: Path,
    compiler: LLMClient,
    critic: LLMClient,
    granted_capabilities: set[Capability],
    required_checks: list[dict[str, Any]] | None = None,
    clarifications: list[str] | None = None,
    token_budget: int | None = None,
) -> CompiledContract:
    discovery = discover_repository(root)
    known_clarifications = list(clarifications or [])[-12:]
    system, user = contract_compile_prompts(goal, discovery, known_clarifications)
    compiler_result = await compiler.complete(
        system,
        user,
        max_tokens=2_000,
        temperature=0.2,
        token_budget=token_budget,
    )
    try:
        proposal = _proposal_from_result(compiler_result.content)
        proposal = _merge_required_checks(proposal, required_checks or [])
    except (TypeError, ValueError) as exc:
        draft = _failed_draft(
            goal=goal,
            discovery=discovery,
            issue=f"The contract compiler returned an invalid draft: {exc}",
            clarifications=known_clarifications,
            compiler_result=compiler_result,
        )
        return CompiledContract(
            draft=draft,
            contract_hash=None,
            compiler_result=compiler_result,
            critic_result=None,
            tokens_spent=compiler_result.tokens,
        )
    tokens_spent = compiler_result.tokens
    critic_result: LLMResult | None = None
    repair_states: set[str] = set()
    for attempt in range(_MAX_CONTRACT_ATTEMPTS):
        effective_proposal = proposal.model_copy(
            update={"checks": _effective_checks(proposal, discovery)}
        )
        critic_system, critic_user = contract_critic_prompts(goal, effective_proposal, discovery)
        remaining = None if token_budget is None else max(0, token_budget - tokens_spent)
        try:
            critic_result = await critic.complete(
                critic_system,
                critic_user,
                max_tokens=1_500,
                temperature=0.1,
                token_budget=remaining,
            )
        except LLMError as exc:
            draft = _draft_from_proposal(
                proposal,
                discovery,
                known_clarifications,
                ContractCritique(
                    accepted=False,
                    issues=[f"The independent contract critic could not complete: {exc}"],
                    question=(
                        "Loop could not independently validate this contract. What observable "
                        "behavior or output must be true when the task is finished?"
                    ),
                ),
                compiler_result,
            )
            return CompiledContract(
                draft=draft,
                contract_hash=None,
                compiler_result=compiler_result,
                critic_result=None,
                tokens_spent=tokens_spent + exc.tokens_spent,
            )
        tokens_spent += critic_result.tokens
        critique = _critique_from_result(critic_result)
        issues = [
            *critique.issues,
            *_deterministic_issues(proposal, discovery, granted_capabilities),
        ]
        issues = list(dict.fromkeys(issue.strip() for issue in issues if issue.strip()))[:12]
        accepted = critique.accepted and not issues
        question = None if accepted else critique.question or _question_for(issues)
        final_critique = critique.model_copy(
            update={"accepted": accepted, "issues": issues, "question": question}
        )
        repair_state = json.dumps(
            {"proposal": proposal.model_dump(mode="json"), "issues": issues},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        stalled = repair_state in repair_states
        if accepted or attempt == _MAX_CONTRACT_ATTEMPTS - 1 or stalled:
            draft = _draft_from_proposal(
                proposal,
                discovery,
                known_clarifications,
                final_critique,
                compiler_result,
            )
            return CompiledContract(
                draft=draft,
                contract_hash=hash_contract(draft) if accepted else None,
                compiler_result=compiler_result,
                critic_result=critic_result,
                tokens_spent=tokens_spent,
            )
        repair_states.add(repair_state)
        repair_system, repair_user = contract_repair_prompts(
            goal,
            proposal,
            discovery,
            issues,
        )
        remaining = None if token_budget is None else max(0, token_budget - tokens_spent)
        try:
            repair_result = await compiler.complete(
                repair_system,
                repair_user,
                max_tokens=2_000,
                temperature=0.1,
                token_budget=remaining,
            )
        except LLMError as exc:
            repair_critique = final_critique.model_copy(
                update={
                    "issues": [
                        *final_critique.issues,
                        f"The bounded contract repair could not complete: {exc}",
                    ][:12]
                }
            )
            draft = _draft_from_proposal(
                proposal,
                discovery,
                known_clarifications,
                repair_critique,
                compiler_result,
            )
            return CompiledContract(
                draft=draft,
                contract_hash=None,
                compiler_result=compiler_result,
                critic_result=critic_result,
                tokens_spent=tokens_spent + exc.tokens_spent,
            )
        tokens_spent += repair_result.tokens
        try:
            proposal = _proposal_from_result(repair_result.content)
            proposal = _merge_required_checks(proposal, required_checks or [])
        except (TypeError, ValueError) as exc:
            repair_critique = final_critique.model_copy(
                update={
                    "issues": [
                        *final_critique.issues,
                        f"The bounded contract repair was invalid: {exc}",
                    ][:12]
                }
            )
            draft = _draft_from_proposal(
                proposal,
                discovery,
                known_clarifications,
                repair_critique,
                compiler_result,
            )
            return CompiledContract(
                draft=draft,
                contract_hash=None,
                compiler_result=compiler_result,
                critic_result=critic_result,
                tokens_spent=tokens_spent,
            )
        compiler_result = repair_result
    raise AssertionError("bounded contract compilation exhausted without a verdict")


def failed_contract_draft(
    *,
    goal: str,
    root: Path,
    issue: str,
    clarifications: list[str] | None = None,
) -> ContractDraft:
    discovery = discover_repository(root)
    return _failed_draft(
        goal=goal,
        discovery=discovery,
        issue=issue,
        clarifications=list(clarifications or [])[-12:],
        compiler_result=None,
    )


def _failed_draft(
    *,
    goal: str,
    discovery: RepositoryDiscovery,
    issue: str,
    clarifications: list[str],
    compiler_result: LLMResult | None,
) -> ContractDraft:
    return ContractDraft(
        criteria=[f"The repository implements the requested outcome: {goal.strip()}"],
        checks=discovery.quality_checks,
        artifacts=[],
        risk="medium",
        assumptions=[],
        confidence=0,
        authority_requests=[],
        compiler=_compiler_identity(compiler_result),
        discovery=discovery,
        clarifications=clarifications,
        critique=ContractCritique(
            accepted=False,
            issues=[issue[:500]],
            question=(
                "Loop could not compile a verifiable contract. What observable behavior or "
                "output must be true when this task is finished?"
            ),
        ),
    )


def _draft_from_proposal(
    proposal: ContractProposal,
    discovery: RepositoryDiscovery,
    clarifications: list[str],
    critique: ContractCritique,
    compiler_result: LLMResult,
) -> ContractDraft:
    return ContractDraft(
        **proposal.model_dump(exclude={"checks"}),
        checks=_effective_checks(proposal, discovery),
        compiler=_compiler_identity(compiler_result),
        discovery=discovery,
        clarifications=clarifications,
        critique=critique,
    )


def _compiler_identity(result: LLMResult | None) -> ContractModelIdentity:
    if result is None:
        return ContractModelIdentity()
    return ContractModelIdentity(provider=result.provider[:80], model=result.model[:160])


def hash_contract(draft: ContractDraft) -> str:
    canonical = json.dumps(
        draft.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_contract_hash(draft: dict[str, Any] | ContractDraft, expected: str) -> bool:
    parsed = draft if isinstance(draft, ContractDraft) else ContractDraft.model_validate(draft)
    return bool(expected) and hash_contract(parsed) == expected


def _proposal_from_result(content: str) -> ContractProposal:
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        raise ValueError("contract compiler returned no JSON object")
    raw_criteria = parsed.get("criteria")
    criteria = list(
        dict.fromkeys(
            text
            for item in (raw_criteria if isinstance(raw_criteria, list) else [])
            if (text := _criterion_text(item))
        )
    )[:12]
    parsed["criteria"] = criteria
    count = min(len(criteria), 12)
    valid_ids = {f"criterion-{index:03d}" for index in range(1, count + 1)}
    normalized_checks: list[dict[str, Any]] = []
    raw_checks = parsed.get("checks")
    bounded_checks = (raw_checks if isinstance(raw_checks, list) else [])[:16]
    for index, raw in enumerate(bounded_checks, start=1):
        if not isinstance(raw, dict):
            continue
        check = dict(raw)
        if check.get("expect_exit") is None:
            check.pop("expect_exit", None)
        if not check.get("kind"):
            command = str(check.get("command") or "").strip()
            path = check.get("path")
            text = str(check.get("text") or "").strip()
            if command and not path and not text:
                check["kind"] = "command"
            elif path and text and not command:
                check["kind"] = "file_contains"
            elif path and not command and not text:
                check["kind"] = "file_exists"
        check["id"] = f"contract-{index:03d}"
        check["source"] = "contract"
        raw_ids = check.get("criterion_ids")
        check["criterion_ids"] = sorted(
            {
                str(value)
                for value in (raw_ids if isinstance(raw_ids, list) else [])
                if str(value) in valid_ids
            }
        )
        normalized_checks.append(check)
    raw_artifacts = parsed.get("artifacts")
    artifacts = list(
        dict.fromkeys(
            str(item).strip()
            for item in (raw_artifacts if isinstance(raw_artifacts, list) else [])
            if str(item).strip()
        )
    )[:16]
    parsed["artifacts"] = artifacts
    existing_artifacts = {
        str(check.get("path"))
        for check in normalized_checks
        if check.get("kind") == "file_exists" and check.get("path")
    }
    for artifact in artifacts:
        if artifact in existing_artifacts:
            continue
        mentioned_by = [
            f"criterion-{index:03d}"
            for index, criterion in enumerate(criteria, start=1)
            if artifact.lower() in criterion.lower()
            or Path(artifact).name.lower() in criterion.lower()
        ]
        normalized_checks.append(
            {
                "id": f"contract-artifact-{len(normalized_checks) + 1:03d}",
                "kind": "file_exists",
                "path": artifact,
                "criterion_ids": mentioned_by,
                "source": "contract",
            }
        )
    raw_authority = parsed.get("authority_requests")
    authority_requests = []
    for value in raw_authority if isinstance(raw_authority, list) else []:
        try:
            authority_requests.append(Capability(str(value)))
        except ValueError:
            continue
    proposal = ContractProposal.model_validate(
        {
            **parsed,
            "checks": normalized_checks,
            "authority_requests": list(dict.fromkeys(authority_requests)),
        }
    )
    return proposal.model_copy(update={"checks": _deduplicate_contract_checks(proposal.checks)})


def _criterion_text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"^criterion-\d{3}\s*:\s*", "", value.strip(), flags=re.I)
    if isinstance(value, dict):
        for key in ("description", "text", "criterion", "outcome"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return re.sub(r"^criterion-\d{3}\s*:\s*", "", text.strip(), flags=re.I)
    return ""


def _check_identity(check: ContractCheck) -> tuple[object, ...]:
    return (
        check.kind,
        (check.command or "").strip(),
        check.path or "",
        check.text or "",
        check.expect_exit,
        check.expect_stdout,
    )


def _check_target(check: ContractCheck) -> tuple[str, str, str]:
    return (check.kind, (check.command or check.path or "").strip(), check.text or "")


def _deduplicate_contract_checks(checks: list[ContractCheck]) -> list[ContractCheck]:
    deduplicated: list[ContractCheck] = []
    positions: dict[tuple[object, ...], int] = {}
    for check in checks:
        identity = _check_identity(check)
        position = positions.get(identity)
        if position is None:
            positions[identity] = len(deduplicated)
            deduplicated.append(check)
            continue
        existing = deduplicated[position]
        deduplicated[position] = existing.model_copy(
            update={"criterion_ids": sorted(set(existing.criterion_ids) | set(check.criterion_ids))}
        )
    return deduplicated


def _effective_checks(
    proposal: ContractProposal,
    discovery: RepositoryDiscovery,
) -> list[ContractCheck]:
    checks = list(proposal.checks)
    targets = {_check_target(check) for check in checks}
    for check in discovery.quality_checks:
        target = _check_target(check)
        if target in targets:
            continue
        checks.append(check)
        targets.add(target)
    return checks


def _merge_required_checks(
    proposal: ContractProposal,
    required_checks: list[dict[str, Any]],
) -> ContractProposal:
    checks = list(proposal.checks)
    artifacts = list(proposal.artifacts)
    for index, raw in enumerate(required_checks[:40], start=1):
        check = ContractCheck.model_validate(
            {
                **raw,
                "id": f"contract-user-{index:03d}",
                "source": "contract",
            }
        )
        checks.append(check)
        if check.kind == "file_exists" and check.path and check.path not in artifacts:
            artifacts.append(check.path)
    return proposal.model_copy(update={"checks": checks, "artifacts": artifacts})


def _critique_from_result(result: LLMResult) -> ContractCritique:
    parsed = _extract_json(result.content)
    if not isinstance(parsed, dict):
        parsed = {
            "accepted": False,
            "issues": ["The independent critic returned no valid verdict."],
            "question": "Please clarify the observable result this task must produce.",
        }
    raw_issues = parsed.get("issues")
    return ContractCritique(
        accepted=parsed.get("accepted") is True,
        issues=[
            str(issue)[:500]
            for issue in (raw_issues if isinstance(raw_issues, list) else [])
            if str(issue).strip()
        ][:12],
        question=(str(parsed["question"])[:1_000] if parsed.get("question") else None),
        provider=result.provider[:80],
        model=result.model[:160],
    )


def _deterministic_issues(
    proposal: ContractProposal,
    discovery: RepositoryDiscovery,
    granted_capabilities: set[Capability],
) -> list[str]:
    issues: list[str] = []
    if proposal.risk != "low":
        issues.append(
            f"Contract risk is {proposal.risk}; only low-risk contracts start automatically."
        )
    if proposal.confidence < _MIN_AUTO_CONFIDENCE:
        issues.append(
            f"Contract confidence is {proposal.confidence}%; automatic start requires at least "
            f"{_MIN_AUTO_CONFIDENCE}%."
        )
    if len(proposal.criteria) > _MAX_AUTO_CRITERIA:
        issues.append(
            f"Contract has {len(proposal.criteria)} criteria; automatic start permits at most "
            f"{_MAX_AUTO_CRITERIA} to prevent over-decomposition."
        )
    for index, criterion in enumerate(proposal.criteria, start=1):
        if any(pattern.fullmatch(criterion.strip()) for pattern in _TAUTOLOGIES):
            issues.append(f"criterion-{index:03d} is tautological rather than observable")
    expected = {f"criterion-{index:03d}" for index in range(1, len(proposal.criteria) + 1)}
    covered = {
        criterion
        for check in proposal.checks
        for criterion in check.criterion_ids
        if check.source == "contract"
    }
    missing = sorted(expected - covered)
    if missing:
        issues.append("No execution check substantiates: " + ", ".join(missing))
    if not proposal.checks:
        issues.append("The contract has no re-runnable execution check.")
    for check in proposal.checks:
        if check.kind == "command" and check.command:
            verdict, reason = evaluate_command(check.command)
            if verdict is Verdict.DENY:
                issues.append(f"Contract check {check.id} is denied by policy: {reason}")
            if Capability.EXEC not in granted_capabilities:
                issues.append(f"Contract check {check.id} requires the exec capability.")
            network_reason = network_command_reason(check.command)
            if network_reason and Capability.NET_SHELL not in granted_capabilities:
                issues.append(
                    f"Contract check {check.id} requires denied shell network access: "
                    f"{network_reason}"
                )
    if discovery.quality_checks and Capability.EXEC not in granted_capabilities:
        issues.append("The discovered repository quality gates require the exec capability.")
    missing_authority = sorted(
        capability.value
        for capability in proposal.authority_requests
        if capability not in granted_capabilities
    )
    if missing_authority:
        issues.append(
            "The draft requests authority the task was not granted: " + ", ".join(missing_authority)
        )
    return issues


def _question_for(issues: list[str]) -> str:
    if any("authority" in issue.lower() for issue in issues):
        return (
            "The task appears to need additional authority. Can it be completed offline inside "
            "this repository, or should you cancel and explicitly enable the required capability?"
        )
    if any(word in issue.lower() for issue in issues for word in ("risk", "confidence")):
        return (
            "The generated contract is not low-risk and high-confidence enough to begin. "
            "Clarify the intended outcome, or publish an explicit acceptance override in "
            "Advanced controls."
        )
    return (
        "The acceptance contract is not yet verifiable. What observable behavior or output "
        "must be true when this task is finished?"
    )


def _is_test_path(path: str) -> bool:
    parts = Path(path).parts
    name = parts[-1].lower()
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    depth = 0
    start = -1
    in_string = False
    escaped = False
    spans: list[str] = []
    for index, char in enumerate(cleaned):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(cleaned[start : index + 1])
    for span in reversed(spans):
        try:
            parsed = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
