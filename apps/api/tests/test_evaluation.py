from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from scripts.evaluate_verified_completion import _build_task_payload

from app.services.evaluation import aggregate_verified_completion, score_verified_completion

EVAL_MANIFEST = Path(__file__).parents[3] / "evals" / "verified-completion.json"


def _fixture() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    task: dict[str, object] = {
        "status": "completed",
        "stop_reason": "goal_achieved",
        "steps_used": 4,
        "tokens_used": 900,
    }
    report: dict[str, object] = {
        "valid": True,
        "receipt": {
            "verified_by": "execution",
            "criteria": [{"id": "criterion-001"}],
            "coverage": {"covered_criteria": ["criterion-001"]},
            "checks": [{"passed": True}],
            "checks_passed": True,
            "files": [{"path": "result.py"}],
        },
    }
    return task, report, {"passed": True}


def test_scores_only_replayable_fully_covered_results_as_solved() -> None:
    task, report, replay = _fixture()
    result = score_verified_completion(task, report, replay, expected_files=["result.py"])
    assert result["solved"] is True
    assert result["false_acceptance"] is False


def test_completed_result_with_failed_replay_is_a_false_acceptance() -> None:
    task, report, _replay = _fixture()
    result = score_verified_completion(
        task, report, {"passed": False}, expected_files=["result.py"]
    )
    assert result["solved"] is False
    assert result["false_acceptance"] is True
    summary = aggregate_verified_completion([result])
    assert summary["solve_rate"] == 0
    assert summary["false_acceptance_rate"] == 1


def test_rejected_stuck_run_is_unsolved_but_not_a_false_acceptance() -> None:
    task, report, replay = _fixture()
    task["stop_reason"] = "stuck"
    result = score_verified_completion(task, report, replay, expected_files=["result.py"])
    assert result["solved"] is False
    assert result["false_acceptance"] is False


def test_aggregate_reports_cost_and_duration() -> None:
    summary = aggregate_verified_completion(
        [
            {"solved": True, "steps_used": 2, "tokens_used": 100, "duration_seconds": 1.5},
            {"solved": False, "steps_used": 4, "tokens_used": 300, "duration_seconds": 2.5},
        ]
    )

    assert summary["total_steps"] == 6
    assert summary["total_tokens"] == 400
    assert summary["total_duration_seconds"] == 4
    assert summary["average_steps"] == 3
    assert summary["average_tokens"] == 200
    assert summary["average_duration_seconds"] == 2


def test_aggregate_rounds_report_metrics_without_float_noise() -> None:
    summary = aggregate_verified_completion(
        [
            {"solved": True, "duration_seconds": 0.1},
            {"solved": False, "duration_seconds": 0.2},
            {"solved": False, "duration_seconds": 0.3},
        ]
    )

    assert summary["solve_rate"] == 0.3333
    assert summary["total_duration_seconds"] == 0.6
    assert summary["average_duration_seconds"] == 0.2


def test_expected_artifacts_are_part_of_the_published_task_contract() -> None:
    payload = _build_task_payload(
        {
            "goal": "Produce a result.",
            "success_criteria": ["The result is correct."],
            "verification_commands": ["test -f result.json"],
            "expected_files": ["result.json", "audit.log"],
        }
    )

    assert payload["success_criteria"] == [
        "The result is correct.",
        "The final workspace contains all required artifacts: `result.json`, `audit.log`.",
    ]
    assert payload["required_artifacts"] == ["result.json", "audit.log"]


def test_eval_commands_use_portable_standard_library_python() -> None:
    cases = json.loads(EVAL_MANIFEST.read_text())["cases"]
    commands = [command for case in cases for command in case["verification_commands"]]

    assert all(not re.search(r"(^|[;&|]\s*)python(?:\s|$)", command) for command in commands)
    assert all("pytest" not in command for command in commands)


def test_semantic_html_oracle_accepts_valid_thead_and_rejects_wrong_rows(tmp_path: Path) -> None:
    cases = json.loads(EVAL_MANIFEST.read_text())["cases"]
    case = next(case for case in cases if case["id"] == "semantic-html-report")
    command = case["verification_commands"][0]
    valid = """<!doctype html>
<html lang="en"><head><title>Scores</title></head><body>
<table><caption>Student scores</caption><thead><tr><th>Name</th><th>Score</th></tr></thead>
<tbody><tr><td>Ada</td><td>98</td></tr><tr><td>Linus</td><td>91</td></tr>
<tr><td>Grace</td><td>95</td></tr></tbody></table></body></html>"""
    (tmp_path / "report.html").write_text(valid)

    accepted = subprocess.run(command, cwd=tmp_path, shell=True, check=False)
    (tmp_path / "report.html").write_text(valid.replace("Grace", "Wrong"))
    rejected = subprocess.run(command, cwd=tmp_path, shell=True, check=False)
    (tmp_path / "report.html").write_text(valid.replace("Student scores", ""))
    empty_caption = subprocess.run(command, cwd=tmp_path, shell=True, check=False)

    assert accepted.returncode == 0
    assert rejected.returncode != 0
    assert empty_caption.returncode != 0


def test_library_cases_require_discoverable_tests_and_external_behavior_oracles() -> None:
    cases = {case["id"]: case for case in json.loads(EVAL_MANIFEST.read_text())["cases"]}

    assert "test_arithmetic.py" in cases["python-library"]["expected_files"]
    assert "test_string_utils.py" in cases["tested-string-utils"]["expected_files"]
    for case_id in ("python-library", "tested-string-utils"):
        command = cases[case_id]["verification_commands"][0]
        assert command.startswith("python3 -m unittest discover -v && python3 -c")
