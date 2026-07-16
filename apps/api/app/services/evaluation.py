from __future__ import annotations

from typing import Any


def score_verified_completion(
    task: dict[str, Any],
    receipt_report: dict[str, Any],
    replay: dict[str, Any],
    *,
    expected_files: list[str],
) -> dict[str, Any]:
    receipt = receipt_report.get("receipt") or {}
    criteria = receipt.get("criteria") or []
    expected_criteria = {
        item.get("id") for item in criteria if isinstance(item, dict) and item.get("id")
    }
    coverage = receipt.get("coverage") or {}
    covered_criteria = set(coverage.get("covered_criteria") or [])
    actual_files = {
        item.get("path")
        for item in receipt.get("files") or []
        if isinstance(item, dict) and item.get("path")
    }
    checks = receipt.get("checks") or []
    accepted = task.get("status") == "completed" and task.get("stop_reason") == "goal_achieved"
    contract_covered = bool(expected_criteria) and expected_criteria <= covered_criteria
    recorded_gate = receipt.get("checks_passed")
    checks_passed = bool(checks) and (
        recorded_gate is True
        if recorded_gate is not None
        else all(bool(check.get("passed")) for check in checks if isinstance(check, dict))
    )
    expected_files_present = set(expected_files) <= actual_files
    execution_verified = receipt.get("verified_by") == "execution"
    integrity_valid = bool(receipt_report.get("valid"))
    replay_passed = bool(replay.get("passed"))
    solved = all(
        (
            accepted,
            execution_verified,
            integrity_valid,
            contract_covered,
            checks_passed,
            replay_passed,
            expected_files_present,
        )
    )
    return {
        "accepted": accepted,
        "solved": solved,
        "false_acceptance": accepted and not solved,
        "execution_verified": execution_verified,
        "integrity_valid": integrity_valid,
        "contract_covered": contract_covered,
        "checks_passed": checks_passed,
        "replay_passed": replay_passed,
        "expected_files_present": expected_files_present,
        "steps_used": task.get("steps_used", 0),
        "tokens_used": task.get("tokens_used", 0),
    }


def aggregate_verified_completion(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    solved = sum(bool(item.get("solved")) for item in results)
    false_acceptances = sum(bool(item.get("false_acceptance")) for item in results)
    total_steps = sum(int(item.get("steps_used", 0)) for item in results)
    total_tokens = sum(int(item.get("tokens_used", 0)) for item in results)
    total_duration_seconds = round(
        sum(float(item.get("duration_seconds", 0)) for item in results), 3
    )
    return {
        "cases": total,
        "solved": solved,
        "solve_rate": round(solved / total, 4) if total else 0.0,
        "false_acceptances": false_acceptances,
        "false_acceptance_rate": round(false_acceptances / total, 4) if total else 0.0,
        "total_steps": total_steps,
        "total_tokens": total_tokens,
        "total_duration_seconds": total_duration_seconds,
        "average_steps": round(total_steps / total, 3) if total else 0.0,
        "average_tokens": round(total_tokens / total, 3) if total else 0.0,
        "average_duration_seconds": round(total_duration_seconds / total, 3) if total else 0.0,
    }
