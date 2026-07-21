"""Deterministic completion contracts for repository tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.verification import CheckResult, execution_coverage_complete


def discover_project_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text()).get("scripts", {})
        except (OSError, ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict):
            runner = _javascript_runner(root)
            for name in ("lint", "typecheck", "test", "build"):
                if isinstance(scripts.get(name), str):
                    checks.append(_command_check(f"system-js-{name}", f"{runner} {name}"))

    pyproject = root / "pyproject.toml"
    pyproject_text = pyproject.read_text(errors="replace") if pyproject.is_file() else ""
    has_python_tests = (root / "tests").is_dir() or any(root.glob("test_*.py"))
    if has_python_tests:
        checks.append(_command_check("system-python-test", "python -m pytest -q"))
    if "[tool.ruff" in pyproject_text:
        checks.append(_command_check("system-python-lint", "ruff check ."))
    if "[tool.mypy" in pyproject_text:
        checks.append(_command_check("system-python-types", "mypy ."))
    return checks


def merge_completion_checks(
    required: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
    *,
    criterion_count: int,
) -> list[dict[str, Any]]:
    criteria = [f"criterion-{index:03d}" for index in range(1, criterion_count + 1)]
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*required, *proposed]:
        check = dict(raw)
        source = str(check.get("source") or "agent")
        check["source"] = source
        if source == "contract" and not check.get("criterion_ids"):
            check["criterion_ids"] = criteria
        key = _check_key(check)
        existing = merged.get(key)
        if existing is None:
            merged[key] = check
            continue
        mapped = {
            str(value)
            for item in (existing, check)
            for value in item.get("criterion_ids", [])
            if str(value).strip()
        }
        existing["criterion_ids"] = sorted(mapped)
        if existing.get("source") == "agent" and source != "agent":
            existing["source"] = source
            existing["id"] = check.get("id", existing.get("id"))
    return list(merged.values())


def declared_contract_coverage_complete(checks: list[dict[str, Any]], criterion_count: int) -> bool:
    if criterion_count <= 0:
        return False
    expected = {f"criterion-{index:03d}" for index in range(1, criterion_count + 1)}
    covered = {
        str(criterion)
        for check in checks
        if check.get("source") == "contract"
        for criterion in check.get("criterion_ids", [])
    }
    return expected <= covered


def attach_baseline(
    results: list[CheckResult], baseline: list[dict[str, Any]]
) -> list[CheckResult]:
    prior = {str(item.get("check_id")): bool(item.get("passed")) for item in baseline}
    for result in results:
        result.baseline_passed = prior.get(result.check_id)
    return results


def completion_gates_pass(results: list[CheckResult]) -> bool:
    for result in results:
        if not result.gating:
            continue
        if result.passed:
            continue
        if result.source == "system" and result.baseline_passed is False:
            continue
        return False
    return True


def mark_supplementary_agent_checks(results: list[CheckResult], criterion_count: int) -> bool:
    contract = [result for result in results if result.source == "contract"]
    authoritative = bool(
        contract
        and completion_gates_pass(contract)
        and execution_coverage_complete(contract, criterion_count)
    )
    if not authoritative:
        return False
    for result in results:
        if result.source != "agent":
            continue
        result.gating = False
        if result.definition is not None:
            result.definition["gating"] = False
    return True


def regressions(results: list[CheckResult]) -> list[CheckResult]:
    return [
        result
        for result in results
        if result.source == "system" and result.baseline_passed is True and not result.passed
    ]


def _javascript_runner(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm run"
    if (root / "yarn.lock").is_file():
        return "yarn run"
    if (root / "bun.lock").is_file() or (root / "bun.lockb").is_file():
        return "bun run"
    return "npm run"


def _command_check(check_id: str, command: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "kind": "command",
        "command": command,
        "expect_exit": 0,
        "source": "system",
    }


def _check_key(check: dict[str, Any]) -> tuple[str, str]:
    kind = str(check.get("kind", ""))
    definition = {
        key: value
        for key, value in check.items()
        if key
        not in {
            "baseline_passed",
            "criterion_ids",
            "definition",
            "evidence",
            "id",
            "passed",
            "source",
        }
    }
    if isinstance(definition.get("command"), str):
        definition["command"] = " ".join(definition["command"].split())
    return kind, json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str)
