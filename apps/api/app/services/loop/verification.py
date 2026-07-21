"""Turn verifier output and execution evidence into one acceptance decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.completion import completion_gates_pass, regressions
from app.services.verification import CheckResult, execution_coverage_complete


def _score(value: object) -> int:
    try:
        return max(0, min(100, int(float(value))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True, slots=True)
class VerificationDecision:
    accepted: bool
    score: int
    missing: tuple[str, ...]
    verified_by: str
    coverage_complete: bool
    gates_passed: bool


class VerificationPolicy:
    def evaluate(
        self,
        payload: Any,
        results: list[CheckResult],
        *,
        criterion_count: int,
        acceptance_score: int,
        strict: bool,
        contract_substantiation_authoritative: bool,
    ) -> VerificationDecision:
        if isinstance(payload, dict):
            score = _score(payload.get("score"))
            raw_missing = payload.get("missing") or []
            missing = [str(item) for item in raw_missing] if isinstance(raw_missing, list) else []
            llm_met = bool(payload.get("met"))
            raw_substantiation = payload.get("checks_substantiate", True)
            substantiates = raw_substantiation is True or (
                isinstance(raw_substantiation, str)
                and raw_substantiation.strip().lower() in {"true", "yes", "1"}
            )
        else:
            score = 0
            missing = ["verifier returned no verdict"]
            llm_met = False
            substantiates = True

        gates_passed = completion_gates_pass(results)
        coverage_complete = execution_coverage_complete(results, criterion_count)
        execution_ready = bool(
            results
            and coverage_complete
            and gates_passed
            and (substantiates or contract_substantiation_authoritative)
        )
        verified_by = "execution" if execution_ready else "judgment"
        accepted = llm_met and score >= acceptance_score and gates_passed
        if strict and not execution_ready:
            accepted = False
            if not coverage_complete:
                missing.append("Every success criterion needs a mapped execution check.")
            if not gates_passed:
                missing.append("A required check failed or a project quality gate regressed.")
            if results and not substantiates and not contract_substantiation_authoritative:
                missing.append("The proposed checks do not substantiate the task goal.")
        for regression in regressions(results):
            missing.append(f"Regression in {regression.target}.")
        return VerificationDecision(
            accepted=accepted,
            score=score,
            missing=tuple(missing),
            verified_by=verified_by,
            coverage_complete=coverage_complete,
            gates_passed=gates_passed,
        )
