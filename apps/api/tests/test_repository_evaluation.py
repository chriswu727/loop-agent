from __future__ import annotations

import json
import shutil
from argparse import Namespace
from pathlib import Path

import httpx
import pytest
from scripts.evaluate_repository_matrix import (
    _assert_identity_unchanged,
    _build_report,
    _evaluation_identity,
    _load_checkpoint,
    _one_shot,
    _publish_task,
    _ungated_loop,
    _write_checkpoint,
)

from app.core.llm import LLMResult
from app.services.repository_evaluation import (
    REQUIRED_CATEGORIES,
    aggregate_repository_results,
    analyze_trajectory,
    apply_file_bundle,
    classify_failure,
    copy_fixture,
    load_repository_manifest,
    protected_file_digests,
    protected_files_unchanged,
    run_external_oracles,
)

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "evals" / "repository-suite.json"
FIXTURES = ROOT / "evals" / "repositories"


def _temporary_projects(root: Path) -> list[Path]:
    return list(root.glob("loop-eval-*"))


class ScriptedLLM:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        token_budget: int | None = None,
    ) -> LLMResult:
        del system, user, max_tokens, temperature, token_budget
        response = self.responses.pop(0)
        return LLMResult(json.dumps(response), provider="scripted", model="scripted-v1", tokens=10)


def test_repository_manifest_covers_gate_four_task_families() -> None:
    manifest = load_repository_manifest(MANIFEST, FIXTURES)
    cases = manifest["cases"]

    assert {case["category"] for case in cases} == REQUIRED_CATEGORIES
    assert sum(case["expected_outcome"] == "clarification" for case in cases) == 1
    assert all(case.get("protected_files") for case in cases)


def test_every_delivery_fixture_begins_with_a_real_failing_oracle(tmp_path: Path) -> None:
    manifest = load_repository_manifest(MANIFEST, FIXTURES)
    for case in manifest["cases"]:
        if case["expected_outcome"] == "clarification":
            continue
        project = tmp_path / case["id"]
        copy_fixture(FIXTURES, case, project)
        evidence = run_external_oracles(project, case["oracle_commands"])
        assert evidence["passed"] is False, case["id"]
        assert evidence["source_unchanged"] is True


def test_file_bundle_is_jailed_and_protected_oracles_detect_tampering(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "source.py").write_text("value = 1\n")
    (project / "test_source.py").write_text("assert True\n")
    protected = protected_file_digests(project, ["test_source.py"])

    apply_file_bundle(project, {"source.py": "value = 2\n"})
    assert protected_files_unchanged(project, protected) is True
    apply_file_bundle(project, {"test_source.py": "assert False\n"})
    assert protected_files_unchanged(project, protected) is False
    with pytest.raises(ValueError, match="unsafe"):
        apply_file_bundle(project, {"../escape.py": "bad"})


def test_oracles_run_twice_on_copies_without_mutating_candidate(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "value.txt").write_text("correct\n")
    evidence = run_external_oracles(
        project,
        [
            'python3 -c "from pathlib import Path; '
            "assert Path('value.txt').read_text() == 'correct\\n'; "
            "Path('oracle-side-effect').write_text('x')\""
        ],
    )

    assert evidence["passed"] is True
    assert evidence["replay_passed"] is True
    assert evidence["source_unchanged"] is True
    assert not (project / "oracle-side-effect").exists()


def test_trajectory_and_failure_taxonomy_are_stable() -> None:
    trajectory = analyze_trajectory(
        [
            {"tool": "read_file", "status": "ok"},
            {"tool": "edit_file", "status": "ok"},
            {"tool": "run_check", "status": "error"},
            {"tool": "finish", "status": "ok"},
        ]
    )

    assert trajectory["mutations"] == 1
    assert trajectory["checks"] == 1
    assert trajectory["finish_attempts"] == 1
    assert trajectory["errors"] == 1
    assert (
        classify_failure(
            {"accepted": True, "solved": False, "oracle_passed": False, "integrity_valid": True}
        )
        == "false-acceptance-oracle"
    )
    assert (
        classify_failure(
            {
                "accepted": False,
                "solved": False,
                "stop_reason": "budget_exhausted",
                "expected_files_present": False,
            }
        )
        == "non-convergence-token-budget"
    )
    assert (
        classify_failure(
            {
                "accepted": False,
                "solved": False,
                "stop_reason": "clarification",
                "expected_files_present": False,
            }
        )
        == "unnecessary-clarification"
    )


def test_aggregate_enforces_three_repeats_and_excludes_safe_clarification() -> None:
    results: list[dict[str, object]] = []
    for run in range(1, 4):
        for index in range(7):
            solved = not (run == 3 and index >= 4)
            results.append(
                {
                    "mode": "full_loop",
                    "case_id": f"case-{index}",
                    "category": "bug-repair",
                    "expected_outcome": "verified_delivery",
                    "solved": solved,
                    "false_acceptance": False,
                    "steps_used": index + 1,
                    "tokens_used": 100 * (index + 1),
                    "duration_seconds": index + 0.5,
                    "questions": 0,
                    "stop_reason": "goal_achieved" if solved else "stuck",
                }
            )
        results.append(
            {
                "mode": "full_loop",
                "case_id": "ambiguous",
                "category": "incomplete-specification",
                "expected_outcome": "clarification",
                "safe_deferral": True,
                "solved": False,
                "false_acceptance": False,
                "questions": 1,
                "stop_reason": "clarification",
            }
        )

    summary = aggregate_repository_results(results)
    full = summary["modes"]["full_loop"]

    assert full["verified_attempts"] == 21
    assert full["solve_rate"] == 0.8571
    assert full["safe_deferrals"] == 3
    assert full["distributions"]["steps_used"]["median"] == 3.5
    assert summary["primary_gate"]["passed"] is True


@pytest.mark.asyncio
async def test_scripted_one_shot_and_ungated_modes_use_the_same_external_oracle(
    tmp_path: Path,
) -> None:
    fixtures = tmp_path / "fixtures"
    fixture = fixtures / "simple"
    fixture.mkdir(parents=True)
    (fixture / "value.py").write_text("VALUE = 1\n")
    case = {
        "id": "simple",
        "category": "bug-repair",
        "fixture": "simple",
        "goal": "Set VALUE to 2.",
        "expected_outcome": "verified_delivery",
        "expected_files": ["value.py"],
        "protected_files": [],
        "oracle_commands": ['python3 -c "import value; assert value.VALUE == 2"'],
        "max_steps": 4,
        "token_budget": 4_000,
    }
    one_shot = await _one_shot(
        ScriptedLLM(
            [{"action": "deliver", "files": {"value.py": "VALUE = 2\n"}, "summary": "done"}]
        ),
        case,
        fixtures,
        tmp_path,
    )
    ungated = await _ungated_loop(
        ScriptedLLM(
            [
                {
                    "tool": "edit_file",
                    "args": {"path": "value.py", "old": "VALUE = 1", "new": "VALUE = 2"},
                },
                {"tool": "run_check", "args": {"index": 0}},
                {"tool": "finish", "args": {"summary": "done"}},
            ]
        ),
        case,
        fixtures,
        tmp_path,
    )

    assert one_shot["solved"] is True
    assert ungated["solved"] is True
    assert ungated["trajectory"]["checks"] == 1
    assert not _temporary_projects(tmp_path)


def test_incomplete_specification_fixture_contains_a_material_conflict(tmp_path: Path) -> None:
    case = next(
        case
        for case in load_repository_manifest(MANIFEST, FIXTURES)["cases"]
        if case["expected_outcome"] == "clarification"
    )
    project = tmp_path / "conflict"
    copy_fixture(FIXTURES, case, project)

    assert "7 days" in (project / "README.md").read_text()
    assert "90 days" in (project / "policy.md").read_text()
    shutil.rmtree(project)


def test_repository_matrix_checkpoint_is_atomic_and_resumable(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"cases": []}\n')
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    output = tmp_path / "results" / "report.json"
    args = Namespace(
        label="test",
        manifest=manifest,
        fixtures_root=fixtures,
        repeats=3,
        timeout=10,
    )
    result = {
        "run": 1,
        "case_id": "case-a",
        "mode": "full_loop",
        "model": {"provider": "test", "model": "same"},
    }
    report = _build_report(
        args,
        ["full_loop"],
        ["case-a"],
        [result],
        run_at="2026-01-01T00:00:00+00:00",
        expected_results=3,
        complete=False,
        identity=_evaluation_identity(args),
    )

    _write_checkpoint(output, report)
    run_at, results = _load_checkpoint(output, args, ["full_loop"], ["case-a"], expected_results=3)

    assert run_at == "2026-01-01T00:00:00+00:00"
    assert results == [result]
    assert json.loads(output.read_text())["complete"] is False
    assert report["manifest"] == str(manifest)
    assert report["model_identity_complete"] is True
    assert output.stat().st_mode & 0o777 == 0o644
    assert not list(output.parent.glob("*.tmp"))


def test_repository_matrix_checkpoint_rejects_changed_identity(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"cases": []}\n')
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    output = tmp_path / "report.json"
    args = Namespace(
        label="test",
        manifest=manifest,
        fixtures_root=fixtures,
        repeats=3,
        timeout=10,
    )
    report = _build_report(
        args,
        ["full_loop"],
        ["case-a"],
        [],
        run_at="2026-01-01T00:00:00+00:00",
        expected_results=3,
        complete=False,
        identity=_evaluation_identity(args),
    )
    _write_checkpoint(output, report)

    with pytest.raises(ValueError, match="modes"):
        _load_checkpoint(output, args, ["one_shot"], ["case-a"], expected_results=3)

    (fixtures / "changed.py").write_text("changed = True\n")
    with pytest.raises(ValueError, match="fixtures_sha256"):
        _load_checkpoint(output, args, ["full_loop"], ["case-a"], expected_results=3)


def test_repository_matrix_rejects_inputs_changed_during_run(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"cases": []}\n')
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    args = Namespace(manifest=manifest, fixtures_root=fixtures)
    identity = _evaluation_identity(args)

    (fixtures / "changed.py").write_text("changed = True\n")

    with pytest.raises(ValueError, match="fixtures_sha256"):
        _assert_identity_unchanged(args, identity)


def test_repository_matrix_waits_and_retries_task_publication_rate_limit() -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "0.1"}),
        httpx.Response(201, json={"id": "task-1"}),
    ]
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://loop.test"
    ) as client:
        published = _publish_task(client, {"goal": "test"}, 10, sleep=sleeps.append)

    assert published == {"id": "task-1"}
    assert sleeps == [0.1]
    assert responses == []
