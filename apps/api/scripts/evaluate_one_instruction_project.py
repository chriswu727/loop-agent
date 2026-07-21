#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.services.evaluation import aggregate_verified_completion, score_verified_completion

ROOT = Path(__file__).parents[3]
DEFAULT_CASES = ROOT / "evals" / "one-instruction-project.json"
TERMINAL = {"completed", "stopped", "failed", "cancelled"}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text())
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation manifest must contain a non-empty cases list")
    return cases


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _seed_project(project_root: Path, case: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    name = f"loop-eval-{case['id']}-{uuid.uuid4().hex[:8]}"
    project = project_root / name
    project.mkdir(parents=False)
    files = case.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"case {case['id']} has no seed files")
    original: dict[str, str] = {}
    for raw_path, raw_content in files.items():
        relative = Path(str(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"case {case['id']} contains an unsafe path")
        content = str(raw_content)
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        original[relative.as_posix()] = content
    _git(project, "init", "--quiet")
    _git(project, "config", "user.email", "loop-eval@example.com")
    _git(project, "config", "user.name", "Loop Evaluation")
    _git(project, "add", "-A")
    _git(project, "commit", "--quiet", "-m", "seed one-instruction fixture")
    return project, original


def _wait_for_task(client: httpx.Client, task_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        if task["status"] in TERMINAL or task["status"] == "awaiting_input":
            return task
        time.sleep(0.5)
    raise TimeoutError(f"task {task_id} did not finish within {timeout:g}s")


def _content_matches(project: Path, expected: dict[str, Any]) -> bool:
    return all(
        (project / path).is_file() and str(fragment) in (project / path).read_text()
        for path, fragment in expected.items()
    )


def _content_restored(project: Path, original: dict[str, str]) -> bool:
    return all((project / path).read_text() == content for path, content in original.items())


def _evaluate_case(
    client: httpx.Client,
    case: dict[str, Any],
    project_root: Path,
    timeout: float,
    *,
    keep_project: bool,
) -> dict[str, Any]:
    project, original = _seed_project(project_root, case)
    started = time.monotonic()
    try:
        response = client.post(
            "/api/v1/tasks",
            json={
                "goal": case["goal"],
                "project_path": project.name,
                "limits": case.get("limits", {"max_steps": 12, "token_budget": 20_000}),
            },
        )
        response.raise_for_status()
        task = _wait_for_task(client, response.json()["id"], timeout)
        receipt_report: dict[str, Any] = {}
        replay: dict[str, Any] = {}
        if task["status"] in TERMINAL:
            receipt_response = client.get(f"/api/v1/tasks/{task['id']}/receipt")
            if receipt_response.status_code == 200:
                receipt_report = receipt_response.json()
                replay_response = client.post(f"/api/v1/tasks/{task['id']}/receipt/replay")
                if replay_response.status_code == 200:
                    replay = replay_response.json()
        scored = score_verified_completion(
            task,
            receipt_report,
            replay,
            expected_files=[str(path) for path in case.get("expected_files", [])],
        )
        receipt = receipt_report.get("receipt") or {}
        receipt_contract = receipt.get("contract") or {}
        draft = task.get("contract") or {}
        critique = draft.get("critique") or {}
        contract_locked = bool(
            task.get("contract_status") == "locked"
            and task.get("contract_hash")
            and task.get("contract_hash") == receipt_contract.get("hash")
            and critique.get("accepted") is True
        )
        apply_passed = False
        undo_passed = False
        apply_error: str | None = None
        undo_error: str | None = None
        applied = False
        if scored["solved"] and contract_locked:
            apply_response = client.post(f"/api/v1/tasks/{task['id']}/changes/apply")
            if apply_response.status_code == 200:
                applied = apply_response.json().get("state") == "applied"
                expected_content = case.get("expected_content")
                apply_passed = (
                    applied
                    and isinstance(expected_content, dict)
                    and _content_matches(project, expected_content)
                )
            else:
                apply_error = apply_response.text[:500]
        if applied:
            undo_response = client.post(f"/api/v1/tasks/{task['id']}/changes/undo")
            if undo_response.status_code == 200:
                undo_passed = (
                    undo_response.json().get("state") == "reverted"
                    and _content_restored(project, original)
                    and _git(project, "status", "--porcelain") == ""
                )
            else:
                undo_error = undo_response.text[:500]
        solved = bool(scored["solved"] and contract_locked and apply_passed and undo_passed)
        accepted = bool(scored["accepted"])
        return {
            "id": case["id"],
            "category": "one-instruction-local-project",
            "task_id": task["id"],
            "status": task["status"],
            "duration_seconds": round(time.monotonic() - started, 3),
            "model": (receipt.get("provenance") or {}).get("model"),
            "isolation": receipt.get("isolation"),
            **scored,
            "accepted": accepted,
            "solved": solved,
            "false_acceptance": accepted and not solved,
            "contract_locked": contract_locked,
            "contract_hash": task.get("contract_hash"),
            "contract_issues": critique.get("issues") or [],
            "apply_passed": apply_passed,
            "undo_passed": undo_passed,
            "apply_error": apply_error,
            "undo_error": undo_error,
        }
    finally:
        if not keep_project:
            shutil.rmtree(project, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Loop's one-instruction local-project flagship path"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-token", default=os.environ.get("LOOP_API_TOKEN"))
    parser.add_argument(
        "--project-root",
        type=Path,
        default=os.environ.get("LOOP_LOCAL_PROJECTS_ROOT"),
        help="the same local-project root configured on the running API",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--label", default="local")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument(
        "--allow-model-spend",
        action="store_true",
        help="required acknowledgement that this evaluation invokes configured models",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_model_spend:
        raise SystemExit("Refusing to invoke models without --allow-model-spend")
    if args.project_root is None:
        raise SystemExit("--project-root or LOOP_LOCAL_PROJECTS_ROOT is required")
    project_root = args.project_root.expanduser().resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {args.api_token}"} if args.api_token else {}
    cases = _load_cases(args.cases)
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as client:
        results = [
            _evaluate_case(
                client,
                case,
                project_root,
                args.timeout,
                keep_project=args.keep_project,
            )
            for case in cases
        ]
    try:
        manifest = str(args.cases.resolve().relative_to(ROOT))
    except ValueError:
        manifest = str(args.cases.resolve())
    report = {
        "schema": "loop.one-instruction-project-eval-report/v1",
        "run_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "summary": aggregate_verified_completion(results),
        "results": results,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    summary = report["summary"]
    return 0 if summary["false_acceptances"] == 0 and summary["solved"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
