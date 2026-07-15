#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.services.evaluation import aggregate_verified_completion, score_verified_completion

ROOT = Path(__file__).parents[3]
DEFAULT_CASES = ROOT / "evals" / "verified-completion.json"
TERMINAL = {"completed", "failed", "cancelled"}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text())
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation manifest must contain a non-empty cases list")
    return cases


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


def _evaluate_case(client: httpx.Client, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = {
        "goal": case["goal"],
        "success_criteria": case["success_criteria"],
        "verification_commands": case["verification_commands"],
        "verification_mode": "strict",
        "limits": case.get("limits", {"max_steps": 20, "token_budget": 30_000}),
    }
    response = client.post("/api/v1/tasks", json=payload)
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
        expected_files=case.get("expected_files", []),
    )
    return {"id": case["id"], "task_id": task["id"], "status": task["status"], **scored}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Loop's Verified Completion benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-token", default=os.environ.get("LOOP_API_TOKEN"))
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument(
        "--allow-model-spend",
        action="store_true",
        help="required acknowledgement that this benchmark invokes configured models",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_model_spend:
        raise SystemExit("Refusing to invoke models without --allow-model-spend")
    headers = {"Authorization": f"Bearer {args.api_token}"} if args.api_token else {}
    cases = _load_cases(args.cases)
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as client:
        results = [_evaluate_case(client, case, args.timeout) for case in cases]
    report = {
        "schema": "loop.verified-completion-eval/v1",
        "run_at": datetime.now(UTC).isoformat(),
        "manifest": str(args.cases),
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
