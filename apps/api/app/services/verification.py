"""Re-execution verification: prove a task is done instead of trusting the claim.

When the agent calls ``finish`` it may attach machine-checkable *checks*. We copy
the workspace to a throwaway directory and re-run every check there with a fresh
tool executor — so a check that passes proves the deliverable actually works on a
clean copy, not just that the agent said so. Checks run through the same command
policy as the agent, so verification can't do anything the agent couldn't.

Supported checks:
  {"kind": "command", "command": "...", "expect_exit": 0, "expect_stdout": "..."}
  {"kind": "file_exists", "path": "relative/path"}
  {"kind": "file_contains", "path": "relative/path", "text": "..."}
"""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.tools import CapabilityEnvelope, ToolExecutor, ToolStatus, Workspace
from app.tools.guards import make_egress_guard


def _leading_exit_code(out: str) -> int | None:
    """Parse the exact exit code from a run_command observation ('exit code N\\n…')."""
    match = re.match(r"exit code (-?\d+)", out)
    return int(match.group(1)) if match else None


@dataclass(slots=True)
class CheckResult:
    kind: str
    target: str  # the command or path the check is about
    passed: bool
    evidence: str  # short proof: exit code + output snippet, or why it failed
    check_id: str = ""
    criterion_ids: tuple[str, ...] = ()
    definition: dict[str, Any] | None = None
    source: str = "agent"
    baseline_passed: bool | None = None
    gating: bool = True


VERIFY_DIR_PREFIX = "verify-"


def sweep_orphaned_verify_dirs(workspaces_root: Path) -> int:
    """Remove ``verify-<uuid>`` workspace copies orphaned by a crash mid-check.
    run_checks removes its copy in a finally, so a leftover means the process died
    during verification. Called at startup, when no verification is in flight — so
    every ``verify-*`` present is a safe-to-delete orphan."""
    swept = 0
    if not workspaces_root.is_dir():
        return 0
    for d in workspaces_root.glob(f"{VERIFY_DIR_PREFIX}*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            swept += 1
    return swept


async def run_checks(
    checks: list[dict[str, Any]],
    source: Workspace,
    *,
    approval_mode: str = "auto",
    command_timeout: int = 60,
    output_limit: int = 4000,
    sandbox_image: str | None = None,
    sandbox_backend: str | None = None,
    sandbox_memory: str = "512m",
    sandbox_cpus: str = "1",
    egress_allowed: bool = False,
    envelope: CapabilityEnvelope | None = None,
    criterion_count: int = 0,
    egress_proxy_url: str | None = None,
    egress_network: str | None = None,
    egress_token_factory: Callable[[], str] | None = None,
    docker_workspace_volume: str | None = None,
    docker_workspace_mount: str | None = None,
    infer_criterion_ids: bool = True,
) -> list[CheckResult]:
    """Re-run every check on its own fresh copy of the workspace. Never raises —
    a bad check definition becomes a failed result the verifier can act on."""
    if not checks:
        return []

    # Copy under the workspaces root (a path the container can bind-mount) rather
    # than system temp, so re-verification runs in the same sandbox as the agent.
    tmp_root = source.root.parent / f"{VERIFY_DIR_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        # Verify under the same authority as the task: default-deny egress unless
        # the task had it (so a check can't reach the network the task couldn't,
        # and a legit network check isn't blocked in a container).
        check_envelope = envelope or CapabilityEnvelope.from_tools(
            None, egress_allowed=egress_allowed
        )
        results: list[CheckResult] = []
        for index, check in enumerate(checks, start=1):
            copy_dir = tmp_root / f"check-{index:03d}"
            await asyncio.to_thread(shutil.copytree, source.root, copy_dir)
            workspace = Workspace(copy_dir)
            executor = ToolExecutor(
                workspace,
                approval_mode=approval_mode,
                command_timeout=command_timeout,
                output_limit=output_limit,
                envelope=check_envelope,
                before_tool=make_egress_guard(check_envelope, workspace),
                sandbox_image=sandbox_image,
                sandbox_backend=sandbox_backend,
                sandbox_memory=sandbox_memory,
                sandbox_cpus=sandbox_cpus,
                egress_proxy_url=egress_proxy_url,
                egress_network=egress_network,
                egress_token_factory=egress_token_factory,
                docker_workspace_volume=docker_workspace_volume,
                docker_workspace_mount=docker_workspace_mount,
            )
            mapped_check = dict(check)
            if infer_criterion_ids and not mapped_check.get("criterion_ids"):
                if criterion_count == 1:
                    mapped_check["criterion_ids"] = ["criterion-001"]
                elif len(checks) == criterion_count:
                    mapped_check["criterion_ids"] = [f"criterion-{index:03d}"]
            results.append(await _run_one(mapped_check, workspace, executor, index=index))
        return results
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp_root, ignore_errors=True)


async def _run_one(
    check: dict[str, Any], workspace: Workspace, executor: ToolExecutor, *, index: int = 1
) -> CheckResult:
    kind = str(check.get("kind", "")).strip()
    check_id = str(check.get("id") or f"check-{index:03d}")[:80]
    raw_criteria = check.get("criterion_ids")
    criterion_ids = tuple(
        str(value)[:80]
        for value in (raw_criteria if isinstance(raw_criteria, list) else [])
        if str(value).strip()
    )
    definition = {key: value for key, value in check.items() if key not in {"passed", "evidence"}}

    def make_result(target: str, passed: bool, evidence: str) -> CheckResult:
        return CheckResult(
            kind or "unknown",
            target,
            passed,
            evidence,
            check_id=check_id,
            criterion_ids=criterion_ids,
            definition=definition,
            source=str(check.get("source") or "agent")[:40],
            gating=check.get("gating") is not False,
        )

    if kind == "command":
        command = str(check.get("command", "")).strip()
        if not command:
            return make_result("", False, "no command given")
        expect_exit = check.get("expect_exit", 0)
        expect_stdout = check.get("expect_stdout")
        tool_result = await executor.execute("run_command", {"command": command})
        out = tool_result.observation
        try:
            want_exit = int(expect_exit)
        except (TypeError, ValueError):
            want_exit = 0
        code = _leading_exit_code(out)  # exact code, so "exit code 1" != "exit code 10"
        exit_ok = code == want_exit
        stdout_ok = (expect_stdout is None) or (str(expect_stdout) in out)
        passed = bool(exit_ok and stdout_ok and tool_result.status is not ToolStatus.BLOCKED)
        return make_result(command, passed, out[:300])

    if kind == "file_exists":
        path = str(check.get("path", "")).strip()
        try:
            exists = workspace.resolve(path).is_file()
        except Exception as exc:
            return make_result(path, False, str(exc)[:200])
        return make_result(path, exists, "found" if exists else "missing")

    if kind == "file_contains":
        path = str(check.get("path", "")).strip()
        text = str(check.get("text", ""))
        try:
            content = workspace.read(path, limit=200_000)
        except Exception as exc:
            return make_result(path, False, str(exc)[:200])
        passed = text in content
        return make_result(path, passed, "contains text" if passed else "text not found")

    return make_result("", False, f"unknown check kind: {kind!r}")


def checks_summary(results: list[CheckResult]) -> str:
    """Human/LLM-readable summary fed back into the verifier prompt."""
    if not results:
        return "(no machine checks were provided)"
    lines = []
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        baseline = ""
        if r.baseline_passed is not None:
            baseline = f"; baseline={'PASS' if r.baseline_passed else 'FAIL'}"
        gating = "" if r.gating else "; supplementary"
        lines.append(f"[{mark}] {r.source} {r.kind} {r.target}: {r.evidence}{baseline}{gating}")
    return "\n".join(lines)


def as_dicts(results: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]


def execution_coverage_complete(results: list[CheckResult], criterion_count: int) -> bool:
    if not results or criterion_count <= 0:
        return False
    expected = {f"criterion-{index:03d}" for index in range(1, criterion_count + 1)}
    covered = {
        criterion for result in results if result.passed for criterion in result.criterion_ids
    }
    return expected <= covered
