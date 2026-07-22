"""Shared evidence and statistics for repository-level model evaluations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

IGNORED_NAMES = {".git", ".next", ".venv", "__pycache__", "node_modules"}
REQUIRED_CATEGORIES = {
    "api",
    "bug-repair",
    "cli",
    "feature-work",
    "incomplete-specification",
    "multi-file-refactor",
    "regression",
    "ui",
}


def load_repository_manifest(path: Path, fixtures_root: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("repository evaluation manifest must be an object")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("repository evaluation manifest needs a non-empty cases list")
    identifiers: set[str] = set()
    categories: set[str] = set()
    resolved_root = fixtures_root.resolve()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("every repository evaluation case must be an object")
        case_id = str(case.get("id", "")).strip()
        if not case_id or case_id in identifiers:
            raise ValueError(f"case id is empty or duplicated: {case_id!r}")
        identifiers.add(case_id)
        category = str(case.get("category", "")).strip()
        categories.add(category)
        outcome = case.get("expected_outcome", "verified_delivery")
        if outcome not in {"verified_delivery", "clarification"}:
            raise ValueError(f"case {case_id} has unsupported expected_outcome")
        fixture = safe_relative_path(str(case.get("fixture", "")))
        fixture_path = (resolved_root / fixture).resolve()
        if resolved_root not in fixture_path.parents or not fixture_path.is_dir():
            raise ValueError(f"case {case_id} fixture is missing or escapes fixtures root")
        commands = case.get("oracle_commands")
        if outcome == "verified_delivery" and (
            not isinstance(commands, list)
            or not commands
            or any(not isinstance(command, str) or not command.strip() for command in commands)
        ):
            raise ValueError(f"case {case_id} needs executable oracle_commands")
        for expected in [*case.get("expected_files", []), *case.get("protected_files", [])]:
            relative = safe_relative_path(str(expected))
            is_protected = expected in case.get("protected_files", [])
            if is_protected and not (fixture_path / relative).is_file():
                raise ValueError(f"case {case_id} protected file is missing: {expected}")
    missing = REQUIRED_CATEGORIES - categories
    if missing:
        raise ValueError(f"repository suite is missing categories: {sorted(missing)}")
    return manifest


def safe_relative_path(raw: str) -> Path:
    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise ValueError(f"unsafe repository-relative path: {raw!r}")
    return relative


def copy_fixture(fixtures_root: Path, case: dict[str, Any], destination: Path) -> None:
    source = fixtures_root.resolve() / safe_relative_path(str(case["fixture"]))
    shutil.copytree(source, destination)
    assert_repository_integrity(destination)


def repository_snapshot(root: Path, *, max_bytes: int = 80_000) -> str:
    blocks: list[str] = []
    used = 0
    for path in _repository_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        block = f"### {relative}\n{content}"
        if used + len(block) > max_bytes:
            remaining = max_bytes - used
            if remaining > 100:
                blocks.append(block[:remaining] + "\n... [snapshot truncated]")
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def apply_file_bundle(root: Path, files: object) -> list[str]:
    if not isinstance(files, dict) or not files:
        raise ValueError("the model response did not contain a non-empty files object")
    changed: list[str] = []
    for raw_path, content in files.items():
        relative = safe_relative_path(str(raw_path))
        target = (root / relative).resolve()
        if root.resolve() not in target.parents:
            raise ValueError(f"file escapes repository: {raw_path!r}")
        if content is None:
            if target.is_file():
                target.unlink()
                changed.append(relative.as_posix())
            continue
        if not isinstance(content, str):
            raise ValueError(f"file content for {raw_path!r} must be a string or null")
        if len(content.encode()) > 1_000_000:
            raise ValueError(f"file content for {raw_path!r} is too large")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        changed.append(relative.as_posix())
    assert_repository_integrity(root)
    return changed


def assert_repository_integrity(root: Path) -> None:
    resolved_root = root.resolve()
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *filenames]:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"repository contains a symlink: {path.relative_to(root)}")
            resolved = path.resolve()
            if resolved != resolved_root and resolved_root not in resolved.parents:
                raise ValueError(f"repository entry escapes root: {path.relative_to(root)}")


def artifact_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _repository_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def protected_file_digests(root: Path, protected_files: list[str]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for raw_path in protected_files:
        relative = safe_relative_path(raw_path)
        target = root / relative
        if not target.is_file() or target.is_symlink():
            raise ValueError(f"protected oracle file is missing: {raw_path}")
        digests[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    return digests


def protected_files_unchanged(root: Path, expected: dict[str, str]) -> bool:
    try:
        return protected_file_digests(root, list(expected)) == expected
    except ValueError:
        return False


def run_external_oracles(
    project: Path,
    commands: list[str],
    *,
    timeout: float = 90,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    source_digest = artifact_digest(project)
    with tempfile.TemporaryDirectory(prefix="loop-oracle-") as raw_temp:
        temp = Path(raw_temp)
        for run_number in (1, 2):
            candidate = temp / f"replay-{run_number}"
            shutil.copytree(project, candidate, ignore=_copy_ignore)
            checks: list[dict[str, Any]] = []
            for command in commands:
                try:
                    completed = subprocess.run(
                        command,
                        cwd=candidate,
                        shell=True,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        env=_safe_subprocess_env(),
                    )
                    output = (completed.stdout + completed.stderr)[-4_000:]
                    checks.append(
                        {
                            "command": command,
                            "passed": completed.returncode == 0,
                            "exit_code": completed.returncode,
                            "output": output,
                        }
                    )
                except subprocess.TimeoutExpired:
                    checks.append(
                        {
                            "command": command,
                            "passed": False,
                            "exit_code": None,
                            "output": f"timed out after {timeout:g}s",
                        }
                    )
            runs.append(
                {
                    "run": run_number,
                    "passed": all(check["passed"] for check in checks),
                    "checks": checks,
                }
            )
    return {
        "passed": bool(runs and runs[0]["passed"]),
        "replay_passed": len(runs) == 2 and runs[1]["passed"],
        "artifact_digest": source_digest,
        "source_unchanged": artifact_digest(project) == source_digest,
        "runs": runs,
    }


def expected_files_present(project: Path, expected_files: list[str]) -> bool:
    return all((project / safe_relative_path(path)).is_file() for path in expected_files)


def analyze_trajectory(steps: list[dict[str, Any]]) -> dict[str, Any]:
    tools = [str(step.get("tool") or "unknown") for step in steps]
    statuses = [str(step.get("status") or "unknown") for step in steps]
    return {
        "tool_counts": dict(sorted(Counter(tools).items())),
        "status_counts": dict(sorted(Counter(statuses).items())),
        "unique_tools": len(set(tools)),
        "mutations": sum(tool in {"write_file", "edit_file"} for tool in tools),
        "checks": sum(tool in {"run_command", "run_check"} for tool in tools),
        "finish_attempts": tools.count("finish"),
        "questions": tools.count("ask_user"),
        "errors": sum(status in {"error", "blocked"} for status in statuses),
    }


def classify_failure(result: dict[str, Any]) -> str | None:
    if result.get("solved") or result.get("safe_deferral"):
        return None
    if result.get("accepted") and not result.get("oracle_passed"):
        return "false-acceptance-oracle"
    if result.get("accepted") and not result.get("integrity_valid"):
        return "false-acceptance-integrity"
    reason = str(result.get("stop_reason") or "")
    if reason in {"max_steps", "token_budget", "stuck"}:
        return f"non-convergence-{reason}"
    if reason == "budget_exhausted":
        return "non-convergence-token-budget"
    if reason in {"awaiting_input", "clarification"}:
        return "unnecessary-clarification"
    if not result.get("expected_files_present", True):
        return "missing-artifact"
    if result.get("error"):
        return "runtime-error"
    return "unverified-output"


def aggregate_repository_results(
    results: list[dict[str, Any]], *, required_repeats: int = 3
) -> dict[str, Any]:
    by_mode: dict[str, Any] = {}
    for mode in sorted({str(item.get("mode")) for item in results}):
        mode_results = [item for item in results if item.get("mode") == mode]
        delivery = [
            item for item in mode_results if item.get("expected_outcome") != "clarification"
        ]
        clarification = [
            item for item in mode_results if item.get("expected_outcome") == "clarification"
        ]
        solved = sum(bool(item.get("solved")) for item in delivery)
        false_acceptances = sum(bool(item.get("false_acceptance")) for item in mode_results)
        distributions = {
            name: _distribution(mode_results, name)
            for name in ("steps_used", "tokens_used", "duration_seconds", "questions")
        }
        by_mode[mode] = {
            "attempts": len(mode_results),
            "verified_attempts": len(delivery),
            "solved": solved,
            "solve_rate": round(solved / len(delivery), 4) if delivery else 0.0,
            "false_acceptances": false_acceptances,
            "false_acceptance_rate": round(false_acceptances / len(mode_results), 4)
            if mode_results
            else 0.0,
            "clarification_attempts": len(clarification),
            "safe_deferrals": sum(bool(item.get("safe_deferral")) for item in clarification),
            "stop_reasons": dict(
                sorted(Counter(str(item.get("stop_reason")) for item in mode_results).items())
            ),
            "failure_classes": dict(
                sorted(
                    Counter(
                        str(item["failure_class"])
                        for item in mode_results
                        if item.get("failure_class")
                    ).items()
                )
            ),
            "distributions": distributions,
            "categories": _category_rates(mode_results),
        }
    primary = by_mode.get("full_loop", {})
    repeats = Counter(
        str(item.get("case_id"))
        for item in results
        if item.get("mode") == "full_loop" and item.get("expected_outcome") != "clarification"
    )
    gate = {
        "required_solve_rate": 0.85,
        "required_repeats": required_repeats,
        "observed_min_repeats": min(repeats.values(), default=0),
        "solve_rate_passed": float(primary.get("solve_rate", 0)) >= 0.85,
        "false_acceptance_passed": primary.get("false_acceptances", 0) == 0,
        "repeats_passed": bool(repeats) and min(repeats.values()) >= required_repeats,
    }
    gate["passed"] = all(
        gate[key] for key in ("solve_rate_passed", "false_acceptance_passed", "repeats_passed")
    )
    comparisons: dict[str, Any] = {}
    if primary:
        for baseline in ("one_shot", "ungated_loop"):
            candidate = by_mode.get(baseline)
            if not candidate:
                continue
            comparisons[f"full_loop_vs_{baseline}"] = {
                "solve_rate_delta": round(
                    float(primary["solve_rate"]) - float(candidate["solve_rate"]), 4
                ),
                "false_acceptance_delta": int(primary["false_acceptances"])
                - int(candidate["false_acceptances"]),
                "median_steps_delta": round(
                    float(primary["distributions"]["steps_used"]["median"])
                    - float(candidate["distributions"]["steps_used"]["median"]),
                    3,
                ),
                "median_tokens_delta": round(
                    float(primary["distributions"]["tokens_used"]["median"])
                    - float(candidate["distributions"]["tokens_used"]["median"]),
                    3,
                ),
                "median_duration_delta": round(
                    float(primary["distributions"]["duration_seconds"]["median"])
                    - float(candidate["distributions"]["duration_seconds"]["median"]),
                    3,
                ),
            }
    return {"modes": by_mode, "comparisons": comparisons, "primary_gate": gate}


def _repository_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in IGNORED_NAMES)
        for filename in sorted(filenames):
            path = Path(current) / filename
            if filename not in IGNORED_NAMES and path.is_file() and not path.is_symlink():
                files.append(path)
    return files


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return set(names) & IGNORED_NAMES


def _safe_subprocess_env() -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _distribution(results: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = [float(item.get(key, 0) or 0) for item in results]
    if not values:
        return {"median": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "median": round(statistics.median(values), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _category_rates(results: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for category in sorted({str(item.get("category")) for item in results}):
        selected = [item for item in results if item.get("category") == category]
        successes = sum(bool(item.get("solved") or item.get("safe_deferral")) for item in selected)
        output[category] = {
            "attempts": len(selected),
            "successes": successes,
            "success_rate": round(successes / len(selected), 4) if selected else 0.0,
        }
    return output
