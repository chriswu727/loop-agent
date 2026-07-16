from __future__ import annotations

from app.services.evaluation import aggregate_verified_completion, score_verified_completion


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

    assert summary["average_steps"] == 3
    assert summary["average_tokens"] == 200
    assert summary["average_duration_seconds"] == 2
