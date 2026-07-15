from __future__ import annotations

import json

from app.services.completion import (
    attach_baseline,
    completion_gates_pass,
    discover_project_checks,
    merge_completion_checks,
    regressions,
)
from app.services.verification import CheckResult, execution_coverage_complete


def test_discovers_python_and_javascript_project_quality_gates(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.mypy]\n")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint .", "test": "vitest", "dev": "next dev"}})
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    checks = discover_project_checks(tmp_path)
    commands = {check["command"] for check in checks}
    assert commands == {"pnpm run lint", "pnpm run test", "pytest -q", "ruff check .", "mypy ."}
    assert all(check["source"] == "system" for check in checks)


def test_contract_checks_are_mapped_and_cannot_be_spoofed_by_agent() -> None:
    checks = merge_completion_checks(
        [
            {
                "id": "contract-001",
                "kind": "command",
                "command": "pytest -q",
                "source": "contract",
            }
        ],
        [
            {
                "id": "fake-system",
                "kind": "command",
                "command": "echo pass",
                "source": "agent",
            }
        ],
        criterion_count=2,
    )
    assert checks[0]["criterion_ids"] == ["criterion-001", "criterion-002"]
    assert checks[1]["source"] == "agent"


def test_distinct_assertions_on_the_same_file_are_not_deduplicated() -> None:
    checks = merge_completion_checks(
        [],
        [
            {"kind": "file_contains", "path": "report.md", "text": "Summary"},
            {"kind": "file_contains", "path": "report.md", "text": "Risks"},
        ],
        criterion_count=2,
    )
    assert [check["text"] for check in checks] == ["Summary", "Risks"]


def test_only_new_system_regressions_block_completion() -> None:
    results = [
        CheckResult("command", "lint", False, "still failing", check_id="lint", source="system"),
        CheckResult("command", "test", False, "regressed", check_id="test", source="system"),
        CheckResult("file_exists", "out", True, "found", check_id="contract", source="contract"),
    ]
    attach_baseline(
        results, [{"check_id": "lint", "passed": False}, {"check_id": "test", "passed": True}]
    )

    assert completion_gates_pass(results) is False
    assert [result.check_id for result in regressions(results)] == ["test"]


def test_failed_mapped_check_does_not_count_as_execution_coverage() -> None:
    failed = CheckResult(
        "command",
        "test",
        False,
        "failed",
        criterion_ids=("criterion-001",),
        source="system",
        baseline_passed=False,
    )
    assert completion_gates_pass([failed]) is True
    assert execution_coverage_complete([failed], 1) is False
