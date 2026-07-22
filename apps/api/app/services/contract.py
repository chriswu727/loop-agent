"""Read-only repository discovery and pre-mutation contract compilation."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.llm import LLMClient, LLMError, LLMResult
from app.core.redaction import redact_secrets
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
_PREVIEW_SUFFIXES = frozenset(
    {
        ".c",
        ".css",
        ".go",
        ".h",
        ".html",
        ".java",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".rs",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)
_PREVIEW_FILES = 24
_PREVIEW_FILE_CHARS = 4_000
_PREVIEW_TOTAL_CHARS = 48_000
_TAUTOLOGIES = (
    re.compile(r"^fully( and correctly)? satisfies? the task[.!]?$", re.I),
    re.compile(r"^(the )?(requested )?(change|task|work) is (complete|correct|done)[.!]?$", re.I),
    re.compile(r"^produce(s)? a correct result[.!]?$", re.I),
    re.compile(r"^works? correctly[.!]?$", re.I),
)
_MIN_AUTO_CONFIDENCE = 80
_MAX_AUTO_CRITERIA = 8
_MAX_CONTRACT_ATTEMPTS = 3
_MAX_GOAL_CRITERION_CHARS = 4_000


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
    bounded_files = files[:500]
    paths = [path for path, _ in bounded_files]
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
    file_previews, previews_truncated = _repository_previews(root, bounded_files, test_files)
    return RepositoryDiscovery(
        manifests=manifests,
        scripts=scripts,
        test_files=test_files,
        build_outputs=build_outputs,
        quality_checks=quality_checks,
        file_previews=file_previews,
        files_scanned=min(len(files), 500),
        truncated=truncated,
        previews_truncated=previews_truncated,
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
    criteria_recovered = False
    try:
        proposal, criteria_recovered = _proposal_from_result(
            compiler_result.content,
            fallback_criteria=[goal],
        )
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
                criteria_recovered=criteria_recovered,
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
        deterministic_issues = _deterministic_issues(
            effective_proposal, discovery, granted_capabilities
        )
        issues = [*critique.issues, *deterministic_issues]
        issues = list(dict.fromkeys(issue.strip() for issue in issues if issue.strip()))[:12]
        adjudication_reason = _deterministic_adjudication_reason(
            effective_proposal,
            discovery,
            critique,
            deterministic_issues,
        )
        adjudicated = adjudication_reason is not None
        accepted = (critique.accepted and not issues) or adjudicated
        question = None if accepted else critique.question or _question_for(issues)
        final_critique = critique.model_copy(
            update={
                "accepted": accepted,
                "issues": issues,
                "question": question,
                "adjudicated": adjudicated,
                "adjudication_reason": adjudication_reason,
            }
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
                criteria_recovered=criteria_recovered,
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
                criteria_recovered=criteria_recovered,
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
            proposal, repair_recovered = _proposal_from_result(
                repair_result.content,
                fallback_criteria=[goal],
            )
            criteria_recovered = criteria_recovered or repair_recovered
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
                criteria_recovered=criteria_recovered,
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
    *,
    criteria_recovered: bool = False,
) -> ContractDraft:
    return ContractDraft(
        **proposal.model_dump(exclude={"checks"}),
        checks=_effective_checks(proposal, discovery),
        criteria_recovery="explicit_user_goal" if criteria_recovered else None,
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
    payload = draft.model_dump(mode="json")
    if payload.get("criteria_recovery") is None:
        payload.pop("criteria_recovery", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_contract_hash(draft: dict[str, Any] | ContractDraft, expected: str) -> bool:
    parsed = draft if isinstance(draft, ContractDraft) else ContractDraft.model_validate(draft)
    return bool(expected) and hash_contract(parsed) == expected


def _proposal_from_result(
    content: str,
    *,
    fallback_criteria: list[str] | None = None,
) -> tuple[ContractProposal, bool]:
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        raise ValueError("contract compiler returned no JSON object")
    raw_criteria = _as_list(parsed.get("criteria"))
    criteria = list(
        dict.fromkeys(text for item in raw_criteria if (text := _criterion_text(item)))
    )[:12]
    recovered = False
    if not criteria:
        criteria = _bounded_fallback_criteria(fallback_criteria or [])
        recovered = bool(criteria)
    parsed["criteria"] = criteria
    count = min(len(criteria), 12)
    valid_ids = {f"criterion-{index:03d}" for index in range(1, count + 1)}
    normalized_checks: list[dict[str, Any]] = []
    bounded_checks = _as_list(parsed.get("checks"))[:16]
    for index, raw in enumerate(bounded_checks, start=1):
        if not isinstance(raw, dict):
            continue
        check = dict(raw)
        if check.get("expect_exit") is None:
            check.pop("expect_exit", None)
        elif str(check.get("expect_exit")).lower() in {
            "!=0",
            "non-zero",
            "non_zero",
            "nonzero",
        }:
            check["expect_exit"] = "nonzero"
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
    raw_artifacts = _as_list(parsed.get("artifacts"))
    artifacts = list(
        dict.fromkeys(str(item).strip() for item in raw_artifacts if str(item).strip())
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
    raw_assumptions = _as_list(parsed.get("assumptions"))
    parsed["assumptions"] = [str(item).strip() for item in raw_assumptions if str(item).strip()][
        :12
    ]
    raw_authority = _as_list(parsed.get("authority_requests"))
    authority_requests = []
    for value in raw_authority:
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
    return (
        proposal.model_copy(update={"checks": _deduplicate_contract_checks(proposal.checks)}),
        recovered,
    )


def _bounded_fallback_criteria(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            text
            for value in values
            if (text := " ".join(value.split())) and len(text) <= _MAX_GOAL_CRITERION_CHARS
        )
    )[:12]


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


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
    positions = {_check_target(check): index for index, check in enumerate(checks)}
    criterion_ids = [f"criterion-{index:03d}" for index in range(1, len(proposal.criteria) + 1)]
    previews_cover_contract = _test_previews_cover_contract(proposal, discovery)
    for check in discovery.quality_checks:
        target = _check_target(check)
        position = positions.get(target)
        if position is not None:
            existing = checks[position]
            if _is_test_command(existing.command):
                checks[position] = existing.model_copy(
                    update={
                        "criterion_ids": sorted(set(existing.criterion_ids) | set(criterion_ids))
                    }
                )
            continue
        if previews_cover_contract and _is_test_command(check.command):
            check = check.model_copy(
                update={
                    "id": f"contract-discovered-test-{len(checks) + 1:03d}",
                    "source": "contract",
                    "criterion_ids": criterion_ids,
                }
            )
        checks.append(check)
        positions[target] = len(checks) - 1
    return _prune_redundant_source_checks(checks, proposal, discovery)


def _prune_redundant_source_checks(
    checks: list[ContractCheck],
    proposal: ContractProposal,
    discovery: RepositoryDiscovery,
) -> list[ContractCheck]:
    expected = {f"criterion-{index:03d}" for index in range(1, len(proposal.criteria) + 1)}
    has_complete_test_gate = any(
        check.source == "contract"
        and _is_test_command(check.command)
        and expected <= set(check.criterion_ids)
        for check in checks
    )
    if not has_complete_test_gate or not discovery.test_files:
        return checks
    artifacts = set(proposal.artifacts)
    previews_cover_contract = _test_previews_cover_contract(proposal, discovery)
    pruned: list[ContractCheck] = []
    for check in checks:
        user_check = check.id.startswith("contract-user-")
        if (
            check.kind == "command"
            and check.source == "contract"
            and not _is_test_command(check.command)
            and not user_check
            and previews_cover_contract
        ):
            continue
        if (
            check.kind in {"file_contains", "file_exists"}
            and check.path
            and not _is_test_path(check.path)
            and not user_check
            and (check.kind == "file_contains" or check.path in artifacts)
        ):
            continue
        pruned.append(check)
    return pruned


def _is_test_command(command: str | None) -> bool:
    if not command:
        return False
    return bool(
        re.search(
            r"(?:^|\s)(?:pytest|py\.test)(?:\s|$)"
            r"|python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest)(?:\s|$)"
            r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)|(?:^|\s)(?:go|cargo)\s+test",
            command,
            re.I,
        )
    )


def _test_previews_cover_contract(
    proposal: ContractProposal, discovery: RepositoryDiscovery
) -> bool:
    if not discovery.test_files or not any(
        _is_test_command(check.command) for check in discovery.quality_checks
    ):
        return False
    if not all(path in discovery.file_previews for path in discovery.test_files):
        return False
    corpus = "\n".join(
        f"{path}\n{discovery.file_previews[path]}" for path in discovery.test_files
    ).lower()
    stopwords = {
        "after",
        "before",
        "command",
        "containing",
        "existing",
        "instead",
        "output",
        "prints",
        "repository",
        "required",
        "returns",
        "should",
        "tests",
        "their",
        "using",
        "when",
        "without",
    }
    for criterion in proposal.criteria:
        lowered = criterion.lower()
        if "test" in lowered and any(word in lowered for word in ("pass", "green", "succeed")):
            continue
        markers = {
            token
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}|\b\d{3}\b", criterion)
            if token.lower() not in stopwords
        }
        covered = {marker for marker in markers if marker.lower() in corpus}
        if len(markers) < 2 or len(covered) < 2:
            return False
    return True


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
            syntax_issue = _inline_python_syntax_issue(check.command)
            if syntax_issue:
                issues.append(f"Contract check {check.id} is invalid: {syntax_issue}")
            if (
                check.expect_stdout is not None
                and "spawnsync" in check.command.lower()
                and not re.search(r"\w+\.stdout", check.command, re.I)
            ):
                issues.append(
                    f"Contract check {check.id} expects child stdout but never forwards it."
                )
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
    for index, criterion in enumerate(proposal.criteria, start=1):
        if "stderr" not in criterion.lower():
            continue
        criterion_id = f"criterion-{index:03d}"
        mapped = [check for check in proposal.checks if criterion_id in check.criterion_ids]
        if any(_is_test_command(check.command) for check in mapped):
            continue
        markers = {
            match.lower()
            for match in re.findall(r"['\"]([a-zA-Z][a-zA-Z0-9_-]{1,40})['\"]", criterion)
        }
        if markers and not any(
            check.expect_stdout and all(marker in check.expect_stdout.lower() for marker in markers)
            for check in mapped
        ):
            issues.append(f"{criterion_id} requires stderr content but no mapped check asserts it.")
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


def _deterministic_adjudication_reason(
    proposal: ContractProposal,
    discovery: RepositoryDiscovery,
    critique: ContractCritique,
    deterministic_issues: list[str],
) -> str | None:
    if critique.accepted or deterministic_issues:
        return None
    issue_is_advisory = [
        _advisory_critic_issue(issue)
        or _critic_issue_refuted_by_test_previews(issue, discovery)
        or _test_runner_speculation_refuted_by_discovery(issue, discovery)
        or _test_coverage_issue_refuted_by_discovery(issue, proposal, discovery)
        for issue in critique.issues
    ]
    if not all(issue_is_advisory):
        return None
    if critique.question and not (
        _internal_contract_question(critique.question)
        or (critique.issues and _generic_contract_question(critique.question))
    ):
        return None
    expected = {f"criterion-{index:03d}" for index in range(1, len(proposal.criteria) + 1)}
    has_complete_test_gate = any(
        check.source == "contract"
        and _is_test_command(check.command)
        and expected <= set(check.criterion_ids)
        for check in proposal.checks
    )
    previews_cover_tests = bool(discovery.test_files) and all(
        path in discovery.file_previews for path in discovery.test_files
    )
    if not has_complete_test_gate or not previews_cover_tests:
        return None
    return (
        "The critic supplied no user-answerable question and the deterministic contract "
        "validator found no blocking issue. Every criterion maps to a discovered test gate "
        "whose test sources were included in bounded repository discovery; the critic's "
        "issues remain recorded as advisory evidence."
    )


def _internal_contract_question(question: str) -> bool:
    lowered = question.lower()
    return any(
        marker in lowered for marker in ("redundan", "rely solely", "remove the", "simplif")
    ) and any(marker in lowered for marker in ("check", "contract", "test suite"))


def _advisory_critic_issue(issue: str) -> bool:
    lowered = issue.lower()
    concerns_checks = any(marker in lowered for marker in ("check", "contract", "test"))
    internal_duplication = any(
        marker in lowered for marker in ("redundan", "duplicat", "simplif", "rely solely")
    )
    mistakes_baseline_for_acceptance = any(
        marker in lowered
        for marker in ("current baseline", "current broken", "current implementation")
    ) and any(marker in lowered for marker in ("fail", "does not", "not pass"))
    return concerns_checks and (internal_duplication or mistakes_baseline_for_acceptance)


def _critic_issue_refuted_by_test_previews(issue: str, discovery: RepositoryDiscovery) -> bool:
    corpus = "\n".join(
        discovery.file_previews.get(path, "").lower() for path in discovery.test_files
    )
    if not corpus:
        return False
    lowered = issue.lower()
    literals = {
        match.lower() for match in re.findall(r"['\"]([a-zA-Z][a-zA-Z0-9_-]{1,40})['\"]", issue)
    }
    channels = {marker for marker in ("stderr", "stdout") if marker in lowered}
    markers = literals | channels
    return len(markers) >= 2 and all(marker in corpus for marker in markers)


def _test_runner_speculation_refuted_by_discovery(
    issue: str, discovery: RepositoryDiscovery
) -> bool:
    lowered = issue.lower()
    speculative = any(
        marker in lowered
        for marker in (
            "does not specify the test file",
            "working directory",
            "may not run the intended tests",
            "uses unittest and the check runs pytest",
        )
    )
    previews_cover_tests = bool(discovery.test_files) and all(
        path in discovery.file_previews for path in discovery.test_files
    )
    return bool(
        speculative
        and previews_cover_tests
        and any(_is_test_command(check.command) for check in discovery.quality_checks)
    )


def _test_coverage_issue_refuted_by_discovery(
    issue: str,
    proposal: ContractProposal,
    discovery: RepositoryDiscovery,
) -> bool:
    lowered = issue.lower()
    concerns_test_evidence = any(
        marker in lowered
        for marker in ("test", "check", "exit code", "stdout", "stderr", "listener")
    )
    substantive_conflict = any(
        marker in lowered
        for marker in ("authority", "contradict", "conflict", "security", "not requested")
    )
    return bool(
        concerns_test_evidence
        and not substantive_conflict
        and _test_previews_cover_contract(proposal, discovery)
    )


def _inline_python_syntax_issue(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"shell syntax could not be parsed ({exc})"
    for index, part in enumerate(parts[:-1]):
        if not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(part).name, re.I):
            continue
        if parts[index + 1] != "-c" or index + 2 >= len(parts):
            continue
        try:
            ast.parse(parts[index + 2])
        except SyntaxError as exc:
            return f"python -c source has a syntax error at line {exc.lineno}"
    return None


def _generic_contract_question(question: str) -> bool:
    lowered = question.lower()
    return "contract" in lowered and "observable behavior" in lowered


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


def _repository_previews(
    root: Path,
    files: list[tuple[str, int]],
    test_files: list[str],
) -> tuple[dict[str, str], bool]:
    sizes = dict(files)
    candidates = [path for path, size in files if size <= 200_000 and _previewable_path(path)]

    def priority(path: str) -> tuple[int, str]:
        name = Path(path).name.lower()
        if path in test_files:
            rank = 0
        elif name.startswith("readme") or name in {"policy.md", "spec.md", "requirements.md"}:
            rank = 1
        elif Path(path).name in _MANIFEST_NAMES:
            rank = 2
        else:
            rank = 3
        return rank, path

    selected = sorted(candidates, key=priority)
    previews: dict[str, str] = {}
    used = 0
    truncated = len(selected) > _PREVIEW_FILES
    for relative in selected[:_PREVIEW_FILES]:
        try:
            content = (root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if settings.agent_redact_secrets:
            content = redact_secrets(content)
        if len(content) > _PREVIEW_FILE_CHARS:
            content = content[:_PREVIEW_FILE_CHARS] + "\n... [file preview truncated]"
            truncated = True
        if used + len(content) > _PREVIEW_TOTAL_CHARS:
            truncated = True
            break
        previews[relative] = content
        used += len(content)
    if any(path not in previews for path in selected) or any(
        sizes[path] > len(previews.get(path, "").encode()) for path in previews
    ):
        truncated = True
    return previews, truncated


def _previewable_path(path: str) -> bool:
    relative = Path(path)
    name = relative.name.lower()
    if name.startswith(".env") or any(
        marker in name for marker in ("credential", "private", "secret")
    ):
        return False
    return relative.suffix.lower() in _PREVIEW_SUFFIXES or relative.name in _MANIFEST_NAMES


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
