#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.llm import LLMClient, get_llm_client
from app.services.evaluation import score_verified_completion
from app.services.loop.decisions import extract_json
from app.services.repository_evaluation import (
    aggregate_repository_results,
    analyze_trajectory,
    apply_file_bundle,
    artifact_digest,
    assert_repository_integrity,
    classify_failure,
    copy_fixture,
    expected_files_present,
    load_repository_manifest,
    protected_file_digests,
    protected_files_unchanged,
    repository_snapshot,
    run_external_oracles,
)
from app.tools import Workspace
from app.tools.base import ToolError

ROOT = Path(__file__).parents[3]
DEFAULT_MANIFEST = ROOT / "evals" / "repository-suite.json"
DEFAULT_FIXTURES = ROOT / "evals" / "repositories"
MODES = ("one_shot", "ungated_loop", "full_loop")
TERMINAL = {"completed", "stopped", "failed", "cancelled"}
DEFAULT_EVAL_TOKEN_BUDGET = 40_000

ONE_SHOT_SYSTEM = """You are solving a small software repository task in one response.
Return JSON only. If the requirements are materially contradictory, return
{"action":"ask_user","question":"..."}. Otherwise return
{"action":"deliver","files":{"relative/path":"complete new contents"},
"summary":"..."}. A null file value deletes that file.
Include every changed file and do not change tests to make failures disappear."""

UNGATED_SYSTEM = """You are an autonomous coding tool loop. There is deliberately no
acceptance contract, critic, verifier gate, progress detector, or completion override in this
baseline. Return exactly one JSON action per turn:
{"thought":"...","tool":"read_file","args":{"path":"..."}}
{"thought":"...","tool":"write_file","args":{"path":"...","content":"..."}}
{"thought":"...","tool":"edit_file","args":{"path":"...","old":"...","new":"..."}}
{"thought":"...","tool":"run_check","args":{"index":0}}
{"thought":"...","tool":"ask_user","args":{"question":"..."}}
{"thought":"...","tool":"finish","args":{"summary":"..."}}
Do not edit tests. Use finish only when you believe the task is complete."""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _initialize_git(project: Path) -> None:
    _git(project, "init", "--quiet")
    _git(project, "config", "user.email", "loop-eval@example.com")
    _git(project, "config", "user.name", "Loop Evaluation")
    _git(project, "add", "-A")
    _git(project, "commit", "--quiet", "-m", "seed repository fixture")


def _seed_project(
    fixtures_root: Path, project_root: Path, case: dict[str, Any], *, git: bool
) -> tuple[Path, str, dict[str, str]]:
    project = project_root / f"loop-eval-{case['id']}-{uuid.uuid4().hex[:8]}"
    copy_fixture(fixtures_root, case, project)
    digest = artifact_digest(project)
    protected = protected_file_digests(project, list(case.get("protected_files", [])))
    if git:
        _initialize_git(project)
    return project, digest, protected


def _oracle_evidence(project: Path, case: dict[str, Any]) -> dict[str, Any]:
    return run_external_oracles(project, list(case.get("oracle_commands", [])))


def _finish_result(result: dict[str, Any]) -> dict[str, Any]:
    result["failure_class"] = classify_failure(result)
    return result


async def _one_shot(
    client: LLMClient,
    case: dict[str, Any],
    fixtures_root: Path,
    project_root: Path,
) -> dict[str, Any]:
    project, initial_digest, protected = _seed_project(fixtures_root, project_root, case, git=False)
    started = time.monotonic()
    tokens = 0
    provider = model = None
    error = None
    action: dict[str, Any] = {}
    changed: list[str] = []
    try:
        prompt = f"Goal:\n{case['goal']}\n\nRepository snapshot:\n{repository_snapshot(project)}"
        response = await client.complete(
            ONE_SHOT_SYSTEM,
            prompt,
            temperature=0,
            max_tokens=8_000,
            token_budget=int(case.get("token_budget", DEFAULT_EVAL_TOKEN_BUDGET)),
        )
        tokens = response.tokens
        provider = response.provider
        model = response.model
        parsed = extract_json(response.content)
        action = parsed if isinstance(parsed, dict) else {}
        if action.get("action") == "deliver":
            changed = apply_file_bundle(project, action.get("files"))
    except Exception as exc:
        error = str(exc)[:1_000]

    is_question = action.get("action") == "ask_user" and bool(action.get("question"))
    accepted = action.get("action") == "deliver" and error is None
    integrity_valid = False
    try:
        assert_repository_integrity(project)
        integrity_valid = protected_files_unchanged(project, protected)
    except ValueError:
        pass
    outcome = str(case.get("expected_outcome", "verified_delivery"))
    if outcome == "clarification":
        safe_deferral = bool(is_question and artifact_digest(project) == initial_digest)
        oracle = {"passed": False, "replay_passed": False, "source_unchanged": True}
        solved = False
    else:
        safe_deferral = False
        oracle = _oracle_evidence(project, case)
        solved = bool(
            accepted
            and integrity_valid
            and oracle["passed"]
            and oracle["replay_passed"]
            and oracle["source_unchanged"]
            and expected_files_present(project, list(case.get("expected_files", [])))
        )
    steps = [
        {
            "tool": "ask_user" if is_question else "finish" if accepted else "invalid",
            "status": "ok" if is_question or accepted else "error",
        }
    ]
    result = {
        "mode": "one_shot",
        "case_id": case["id"],
        "category": case["category"],
        "expected_outcome": outcome,
        "accepted": accepted,
        "solved": solved,
        "safe_deferral": safe_deferral,
        "false_acceptance": accepted and not solved,
        "oracle_passed": bool(oracle["passed"]),
        "replay_passed": bool(oracle["replay_passed"]),
        "integrity_valid": integrity_valid,
        "expected_files_present": expected_files_present(
            project, list(case.get("expected_files", []))
        ),
        "artifact_digest": artifact_digest(project),
        "changed_files": changed,
        "steps_used": 1,
        "tokens_used": tokens,
        "duration_seconds": round(time.monotonic() - started, 3),
        "questions": int(is_question),
        "stop_reason": (
            "clarification" if is_question else "goal_achieved" if accepted else "invalid_response"
        ),
        "provider": provider,
        "model": {"provider": provider, "model": model} if model else None,
        "isolation": "temporary-directory",
        "trajectory": analyze_trajectory(steps),
        "contract_quality": None,
        "error": error,
    }
    shutil.rmtree(project, ignore_errors=True)
    return _finish_result(result)


async def _ungated_loop(
    client: LLMClient,
    case: dict[str, Any],
    fixtures_root: Path,
    project_root: Path,
) -> dict[str, Any]:
    project, initial_digest, protected = _seed_project(fixtures_root, project_root, case, git=False)
    workspace = Workspace(project)
    started = time.monotonic()
    max_steps = int(case.get("max_steps", 12))
    token_budget = int(case.get("token_budget", DEFAULT_EVAL_TOKEN_BUDGET))
    tokens = 0
    provider = model = None
    accepted = is_question = False
    error = None
    stop_reason = "max_steps"
    observation = "No actions have run yet."
    steps: list[dict[str, Any]] = []

    for number in range(1, max_steps + 1):
        remaining = token_budget - tokens
        if remaining < 1_000:
            stop_reason = "token_budget"
            break
        prompt = (
            f"Goal:\n{case['goal']}\n\nTrusted checks (use run_check by index):\n"
            + "\n".join(
                f"{index}: {command}"
                for index, command in enumerate(case.get("oracle_commands", []))
            )
            + f"\n\nCurrent repository:\n{repository_snapshot(project, max_bytes=50_000)}"
            + f"\n\nPrevious observation:\n{observation}"
        )
        try:
            response = await client.complete(
                UNGATED_SYSTEM,
                prompt,
                temperature=0,
                max_tokens=min(6_000, remaining),
                token_budget=remaining,
            )
            tokens += response.tokens
            provider = response.provider
            model = response.model
            parsed = extract_json(response.content)
            action: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
            tool = str(action.get("tool") or "invalid")
            raw_args = action.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            status = "ok"
            try:
                if tool == "read_file":
                    observation = workspace.read(str(args.get("path", "")))
                elif tool == "write_file":
                    observation = workspace.write(
                        str(args.get("path", "")), str(args.get("content", ""))
                    )
                elif tool == "edit_file":
                    observation = workspace.edit(
                        str(args.get("path", "")),
                        str(args.get("old", "")),
                        str(args.get("new", "")),
                    )
                elif tool == "run_check":
                    index = int(args.get("index", -1))
                    commands = list(case.get("oracle_commands", []))
                    if index < 0 or index >= len(commands):
                        raise ValueError("run_check index is outside the trusted check list")
                    evidence = run_external_oracles(project, [commands[index]])
                    check = evidence["runs"][0]["checks"][0]
                    observation = (
                        f"exit code {check['exit_code']}\n{check['output']}"
                        if check["output"]
                        else f"exit code {check['exit_code']}"
                    )
                    if not check["passed"]:
                        status = "error"
                elif tool == "ask_user":
                    is_question = bool(str(args.get("question", "")).strip())
                    observation = str(args.get("question", ""))
                    stop_reason = "clarification"
                elif tool == "finish":
                    accepted = True
                    observation = str(args.get("summary", ""))
                    stop_reason = "goal_achieved"
                else:
                    status = "error"
                    observation = "Invalid action. Return one documented JSON tool action."
            except (ToolError, ValueError, TypeError) as exc:
                status = "error"
                observation = str(exc)
            steps.append({"number": number, "tool": tool, "status": status})
            if accepted or is_question:
                break
        except Exception as exc:
            error = str(exc)[:1_000]
            stop_reason = "runtime_error"
            break

    integrity_valid = False
    try:
        assert_repository_integrity(project)
        integrity_valid = protected_files_unchanged(project, protected)
    except ValueError:
        pass
    outcome = str(case.get("expected_outcome", "verified_delivery"))
    if outcome == "clarification":
        safe_deferral = bool(is_question and artifact_digest(project) == initial_digest)
        oracle = {"passed": False, "replay_passed": False, "source_unchanged": True}
        solved = False
    else:
        safe_deferral = False
        oracle = _oracle_evidence(project, case)
        solved = bool(
            accepted
            and integrity_valid
            and oracle["passed"]
            and oracle["replay_passed"]
            and oracle["source_unchanged"]
            and expected_files_present(project, list(case.get("expected_files", [])))
        )
    trajectory = analyze_trajectory(steps)
    result = {
        "mode": "ungated_loop",
        "case_id": case["id"],
        "category": case["category"],
        "expected_outcome": outcome,
        "accepted": accepted,
        "solved": solved,
        "safe_deferral": safe_deferral,
        "false_acceptance": accepted and not solved,
        "oracle_passed": bool(oracle["passed"]),
        "replay_passed": bool(oracle["replay_passed"]),
        "integrity_valid": integrity_valid,
        "expected_files_present": expected_files_present(
            project, list(case.get("expected_files", []))
        ),
        "artifact_digest": artifact_digest(project),
        "steps_used": len(steps),
        "tokens_used": tokens,
        "duration_seconds": round(time.monotonic() - started, 3),
        "questions": trajectory["questions"],
        "stop_reason": stop_reason,
        "provider": provider,
        "model": {"provider": provider, "model": model} if model else None,
        "isolation": "temporary-directory/trusted-checks-only",
        "trajectory": trajectory,
        "contract_quality": None,
        "error": error,
    }
    shutil.rmtree(project, ignore_errors=True)
    return _finish_result(result)


def _wait_for_task(client: httpx.Client, task_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}")
        response.raise_for_status()
        raw_task = response.json()
        if not isinstance(raw_task, dict):
            raise ValueError("task response must be an object")
        task: dict[str, Any] = raw_task
        if task["status"] in TERMINAL or task["status"] == "awaiting_input":
            return task
        time.sleep(0.5)
    raise TimeoutError(f"task {task_id} did not finish within {timeout:g}s")


def _publish_task(
    api: httpx.Client,
    payload: dict[str, Any],
    timeout: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = api.post("/api/v1/tasks", json=payload)
        if response.status_code != 429:
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("task publication response must be an object")
            return body
        try:
            delay = max(0.1, float(response.headers.get("Retry-After", "60")))
        except ValueError:
            delay = 60.0
        if time.monotonic() + delay >= deadline:
            raise TimeoutError("task publication remained rate limited")
        sleep(delay)


def _full_loop(
    api: httpx.Client,
    case: dict[str, Any],
    fixtures_root: Path,
    project_root: Path,
    timeout: float,
) -> dict[str, Any]:
    project, initial_digest, protected = _seed_project(fixtures_root, project_root, case, git=True)
    started = time.monotonic()
    task: dict[str, Any] = {}
    receipt_report: dict[str, Any] = {}
    replay: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    external = {"passed": False, "replay_passed": False, "source_unchanged": True}
    apply_passed = undo_passed = False
    replay_error = apply_error = undo_error = None
    error = None
    try:
        published = _publish_task(
            api,
            {
                "goal": case["goal"],
                "project_path": project.name,
                "limits": {
                    "max_steps": int(case.get("max_steps", 12)),
                    "token_budget": int(case.get("token_budget", DEFAULT_EVAL_TOKEN_BUDGET)),
                },
            },
            timeout,
        )
        task = _wait_for_task(api, published["id"], timeout)
        steps_response = api.get(f"/api/v1/tasks/{task['id']}/steps")
        if steps_response.status_code == 200:
            steps = steps_response.json()
        if task["status"] in TERMINAL:
            receipt_response = api.get(f"/api/v1/tasks/{task['id']}/receipt")
            if receipt_response.status_code == 200:
                receipt_report = receipt_response.json()
                replay_response = api.post(f"/api/v1/tasks/{task['id']}/receipt/replay")
                if replay_response.status_code == 200:
                    replay = replay_response.json()
                else:
                    replay_error = replay_response.text[:1_000]
        outcome = str(case.get("expected_outcome", "verified_delivery"))
        if outcome == "verified_delivery" and task.get("status") in TERMINAL:
            preliminary = score_verified_completion(
                task,
                receipt_report,
                replay,
                expected_files=list(case.get("expected_files", [])),
            )
            if preliminary["solved"]:
                apply_response = api.post(f"/api/v1/tasks/{task['id']}/changes/apply")
                if apply_response.status_code == 200:
                    apply_passed = apply_response.json().get("state") == "applied"
                else:
                    apply_error = apply_response.text[:1_000]
                if apply_passed:
                    external = _oracle_evidence(project, case)
                    undo_response = api.post(f"/api/v1/tasks/{task['id']}/changes/undo")
                    undo_passed = bool(
                        undo_response.status_code == 200
                        and undo_response.json().get("state") == "reverted"
                        and artifact_digest(project) == initial_digest
                        and _git(project, "status", "--porcelain") == ""
                    )
                    if undo_response.status_code != 200:
                        undo_error = undo_response.text[:1_000]
    except Exception as exc:
        error = str(exc)[:1_000]

    outcome = str(case.get("expected_outcome", "verified_delivery"))
    accepted = task.get("status") == "completed" and task.get("stop_reason") == "goal_achieved"
    trajectory = analyze_trajectory(steps)
    receipt = receipt_report.get("receipt") or {}
    contract = task.get("contract") or {}
    critique = contract.get("critique") or {}
    receipt_contract = receipt.get("contract") or {}
    contract_locked = bool(
        task.get("contract_status") == "locked"
        and task.get("contract_hash")
        and task.get("contract_hash") == receipt_contract.get("hash")
        and critique.get("accepted") is True
    )
    integrity_valid = bool(
        protected_files_unchanged(project, protected)
        and (not receipt_report or receipt_report.get("valid"))
        and external.get("source_unchanged", True)
    )
    if outcome == "clarification":
        safe_deferral = bool(
            task.get("status") == "awaiting_input"
            and task.get("pending_question")
            and artifact_digest(project) == initial_digest
            and _git(project, "status", "--porcelain") == ""
        )
        solved = False
    else:
        safe_deferral = False
        preliminary = score_verified_completion(
            task,
            receipt_report,
            replay,
            expected_files=list(case.get("expected_files", [])),
        )
        solved = bool(
            preliminary["solved"]
            and contract_locked
            and apply_passed
            and external["passed"]
            and external["replay_passed"]
            and integrity_valid
            and undo_passed
        )
    model = (receipt.get("provenance") or {}).get("model") or contract.get("compiler")
    if model is None and steps:
        model = {"provider": "unreported", "model": "unreported"}
    receipt_file_paths = {
        str(item.get("path"))
        for item in receipt.get("files") or []
        if isinstance(item, dict) and item.get("path")
    }
    expected_receipt_files = set(case.get("expected_files", [])) <= receipt_file_paths
    result = {
        "mode": "full_loop",
        "case_id": case["id"],
        "category": case["category"],
        "expected_outcome": outcome,
        "task_id": task.get("id"),
        "accepted": accepted,
        "solved": solved,
        "safe_deferral": safe_deferral,
        "false_acceptance": accepted and not solved,
        "oracle_passed": bool(external["passed"]),
        "replay_passed": bool(replay.get("passed") and external["replay_passed"]),
        "integrity_valid": integrity_valid,
        "expected_files_present": expected_files_present(
            project, list(case.get("expected_files", []))
        )
        if apply_passed and not undo_passed
        else expected_receipt_files
        if outcome == "verified_delivery"
        else True,
        "artifact_digest": external.get("artifact_digest"),
        "apply_passed": apply_passed,
        "apply_error": apply_error,
        "undo_passed": undo_passed,
        "undo_error": undo_error,
        "receipt_replay_error": replay_error,
        "steps_used": int(task.get("steps_used", len(steps)) or 0),
        "tokens_used": int(task.get("tokens_used", 0) or 0),
        "duration_seconds": round(time.monotonic() - started, 3),
        "questions": max(trajectory["questions"], int(bool(task.get("pending_question")))),
        "stop_reason": task.get("stop_reason")
        or ("clarification" if task.get("status") == "awaiting_input" else "runtime_error"),
        "provider": model.get("provider") if isinstance(model, dict) else None,
        "model": model,
        "isolation": receipt.get("isolation") or task.get("sandbox"),
        "trajectory": trajectory,
        "contract_quality": {
            "locked": contract_locked,
            "critic_accepted": critique.get("accepted") is True,
            "critic_adjudicated": critique.get("adjudicated") is True,
            "adjudication_reason": critique.get("adjudication_reason"),
            "criteria": len(contract.get("criteria") or []),
            "checks": len(contract.get("checks") or []),
            "issues": critique.get("issues") or [],
            "question": critique.get("question"),
        },
        "error": error or task.get("error"),
    }
    shutil.rmtree(project, ignore_errors=True)
    return _finish_result(result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare one-shot, ungated, and full Loop")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--fixtures-root", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-token", default=os.environ.get("LOOP_API_TOKEN"))
    parser.add_argument(
        "--project-root", type=Path, default=os.environ.get("LOOP_LOCAL_PROJECTS_ROOT")
    )
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--label", default="local")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume an interrupted run from --output after validating its matrix identity",
    )
    parser.add_argument(
        "--allow-model-spend",
        action="store_true",
        help="required acknowledgement that every selected mode invokes the configured model",
    )
    return parser


def _build_report(
    args: argparse.Namespace,
    modes: list[str],
    case_ids: list[str],
    results: list[dict[str, Any]],
    *,
    run_at: str,
    expected_results: int,
    complete: bool,
    identity: dict[str, str],
) -> dict[str, Any]:
    model_identities = {
        json.dumps(result["model"], sort_keys=True) for result in results if result.get("model")
    }
    models = [json.loads(identity) for identity in sorted(model_identities)]
    model_identity_complete = all(result.get("model") for result in results)
    return {
        "schema": "loop.repository-eval-report/v1",
        "run_at": run_at,
        "label": args.label,
        "manifest": _portable_path(args.manifest),
        "manifest_sha256": identity["manifest_sha256"],
        "fixtures": _portable_path(args.fixtures_root),
        "fixtures_sha256": identity["fixtures_sha256"],
        "evaluation_runtime_sha256": identity["evaluation_runtime_sha256"],
        "modes": modes,
        "repeats": args.repeats,
        "selected_case_ids": case_ids,
        "expected_results": expected_results,
        "completed_results": len(results),
        "complete": complete,
        "models": models,
        "same_model_across_modes": len(models) == 1 and bool(models) and model_identity_complete,
        "model_identity_complete": model_identity_complete,
        "limits": {
            "default_token_budget": DEFAULT_EVAL_TOKEN_BUDGET,
            "task_timeout_seconds": args.timeout,
            "llm_request_timeout_seconds": settings.llm_timeout_seconds,
            "llm_total_timeout_seconds": settings.llm_total_timeout_seconds,
            "llm_max_retries": settings.llm_max_retries,
        },
        "summary": aggregate_repository_results(results, required_repeats=3),
        "results": results,
    }


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _evaluation_runtime_sha256() -> str:
    digest = hashlib.sha256()
    digest.update(artifact_digest(ROOT / "apps" / "api" / "app").encode())
    digest.update(hashlib.sha256(Path(__file__).read_bytes()).digest())
    return digest.hexdigest()


def _evaluation_identity(args: argparse.Namespace) -> dict[str, str]:
    return {
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "fixtures_sha256": artifact_digest(args.fixtures_root),
        "evaluation_runtime_sha256": _evaluation_runtime_sha256(),
    }


def _assert_identity_unchanged(args: argparse.Namespace, identity: dict[str, str]) -> None:
    current = _evaluation_identity(args)
    changed = [key for key, value in identity.items() if current.get(key) != value]
    if changed:
        raise ValueError("evaluation inputs changed during run: " + ", ".join(changed))


def _write_checkpoint(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_checkpoint(
    path: Path,
    args: argparse.Namespace,
    modes: list[str],
    case_ids: list[str],
    expected_results: int,
    identity: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load checkpoint {path}: {exc}") from exc
    expected = {
        "schema": "loop.repository-eval-report/v1",
        "label": args.label,
        **(identity or _evaluation_identity(args)),
        "modes": modes,
        "repeats": args.repeats,
        "selected_case_ids": case_ids,
        "expected_results": expected_results,
    }
    mismatches = [key for key, value in expected.items() if report.get(key) != value]
    if mismatches:
        raise ValueError("checkpoint does not match this matrix: " + ", ".join(mismatches))
    results = report.get("results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise ValueError("checkpoint results must be a list of objects")
    valid_keys = {
        (repeat, case_id, mode)
        for repeat in range(1, args.repeats + 1)
        for case_id in case_ids
        for mode in modes
    }
    observed_keys = [(item.get("run"), item.get("case_id"), item.get("mode")) for item in results]
    if len(set(observed_keys)) != len(observed_keys):
        raise ValueError("checkpoint contains duplicate matrix results")
    if not set(observed_keys) <= valid_keys:
        raise ValueError("checkpoint contains results outside this matrix")
    run_at = report.get("run_at")
    if not isinstance(run_at, str) or not run_at:
        raise ValueError("checkpoint is missing run_at")
    return run_at, results


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_repository_manifest(args.manifest, args.fixtures_root)
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    if not modes:
        raise ValueError("--modes must select at least one mode")
    if len(set(modes)) != len(modes):
        raise ValueError("--modes must not contain duplicates")
    unknown = set(modes) - set(MODES)
    if unknown:
        raise ValueError(f"unknown modes: {sorted(unknown)}")
    if args.repeats < 1:
        raise ValueError("--repeats must be positive")
    cases = manifest["cases"]
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case["id"] in selected]
        missing = selected - {case["id"] for case in cases}
        if missing:
            raise ValueError(f"unknown case ids: {sorted(missing)}")
    if "full_loop" in modes and args.project_root is None:
        raise ValueError("--project-root or LOOP_LOCAL_PROJECTS_ROOT is required for full_loop")
    if args.resume and args.output is None:
        raise ValueError("--resume requires --output")

    headers = {"Authorization": f"Bearer {args.api_token}"} if args.api_token else {}
    llm = get_llm_client()
    case_ids = [str(case["id"]) for case in cases]
    expected_results = args.repeats * len(cases) * len(modes)
    identity = _evaluation_identity(args)
    run_at = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []
    if args.resume:
        if not args.output.exists():
            raise ValueError(f"checkpoint does not exist: {args.output}")
        run_at, results = _load_checkpoint(
            args.output,
            args,
            modes,
            case_ids,
            expected_results,
            identity,
        )
    completed = {
        (int(result["run"]), str(result["case_id"]), str(result["mode"])) for result in results
    }
    with tempfile.TemporaryDirectory(prefix="loop-repository-matrix-") as raw_temp:
        baseline_root = Path(raw_temp)
        project_root = (
            args.project_root.expanduser().resolve() if args.project_root else baseline_root
        )
        project_root.mkdir(parents=True, exist_ok=True)
        with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as api:
            for repeat in range(1, args.repeats + 1):
                for case in cases:
                    for mode in modes:
                        result_key = (repeat, str(case["id"]), mode)
                        if result_key in completed:
                            continue
                        if mode == "one_shot":
                            result = await _one_shot(llm, case, args.fixtures_root, baseline_root)
                        elif mode == "ungated_loop":
                            result = await _ungated_loop(
                                llm, case, args.fixtures_root, baseline_root
                            )
                        else:
                            result = _full_loop(
                                api,
                                case,
                                args.fixtures_root,
                                project_root,
                                args.timeout,
                            )
                        _assert_identity_unchanged(args, identity)
                        result["run"] = repeat
                        results.append(result)
                        completed.add(result_key)
                        if args.output:
                            _write_checkpoint(
                                args.output,
                                _build_report(
                                    args,
                                    modes,
                                    case_ids,
                                    results,
                                    run_at=run_at,
                                    expected_results=expected_results,
                                    complete=False,
                                    identity=identity,
                                ),
                            )

    _assert_identity_unchanged(args, identity)
    return _build_report(
        args,
        modes,
        case_ids,
        results,
        run_at=run_at,
        expected_results=expected_results,
        complete=len(results) == expected_results,
        identity=identity,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_model_spend:
        raise SystemExit("Refusing to invoke models without --allow-model-spend")
    report = asyncio.run(_run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        _write_checkpoint(args.output, report)
    print(rendered)
    return 0 if report["complete"] and report["summary"]["primary_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
