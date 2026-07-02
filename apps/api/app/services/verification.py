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
from dataclasses import asdict, dataclass
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


async def run_checks(
    checks: list[dict[str, Any]],
    source: Workspace,
    *,
    approval_mode: str = "auto",
    command_timeout: int = 60,
    output_limit: int = 4000,
    sandbox_image: str | None = None,
    sandbox_memory: str = "512m",
    sandbox_cpus: str = "1",
    egress_allowed: bool = False,
) -> list[CheckResult]:
    """Re-run each check on a fresh copy of the workspace. Never raises — a bad
    check definition becomes a failed result the verifier can act on."""
    if not checks:
        return []

    # Copy under the workspaces root (a path the container can bind-mount) rather
    # than system temp, so re-verification runs in the same sandbox as the agent.
    tmp_root = source.root.parent / f"verify-{uuid.uuid4().hex[:12]}"
    try:
        copy_dir = tmp_root / "ws"
        await asyncio.to_thread(shutil.copytree, source.root, copy_dir)
        workspace = Workspace(copy_dir)
        # Verify under the same authority as the task: default-deny egress unless
        # the task had it (so a check can't reach the network the task couldn't,
        # and a legit network check isn't blocked in a container).
        envelope = CapabilityEnvelope.from_tools(None, egress_allowed=egress_allowed)
        executor = ToolExecutor(
            workspace,
            approval_mode=approval_mode,
            command_timeout=command_timeout,
            output_limit=output_limit,
            envelope=envelope,
            before_tool=make_egress_guard(envelope),
            sandbox_image=sandbox_image,
            sandbox_memory=sandbox_memory,
            sandbox_cpus=sandbox_cpus,
        )
        results: list[CheckResult] = []
        for check in checks:
            results.append(await _run_one(check, workspace, executor))
        return results
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp_root, ignore_errors=True)


async def _run_one(
    check: dict[str, Any], workspace: Workspace, executor: ToolExecutor
) -> CheckResult:
    kind = str(check.get("kind", "")).strip()

    if kind == "command":
        command = str(check.get("command", "")).strip()
        if not command:
            return CheckResult("command", "", False, "no command given")
        expect_exit = check.get("expect_exit", 0)
        expect_stdout = check.get("expect_stdout")
        result = await executor.execute("run_command", {"command": command})
        out = result.observation
        try:
            want_exit = int(expect_exit)
        except (TypeError, ValueError):
            want_exit = 0
        code = _leading_exit_code(out)  # exact code, so "exit code 1" != "exit code 10"
        exit_ok = code == want_exit
        stdout_ok = (expect_stdout is None) or (str(expect_stdout) in out)
        passed = bool(exit_ok and stdout_ok and result.status is not ToolStatus.BLOCKED)
        return CheckResult("command", command, passed, out[:300])

    if kind == "file_exists":
        path = str(check.get("path", "")).strip()
        try:
            exists = workspace.resolve(path).is_file()
        except Exception as exc:
            return CheckResult("file_exists", path, False, str(exc)[:200])
        return CheckResult("file_exists", path, exists, "found" if exists else "missing")

    if kind == "file_contains":
        path = str(check.get("path", "")).strip()
        text = str(check.get("text", ""))
        try:
            content = workspace.read(path, limit=200_000)
        except Exception as exc:
            return CheckResult("file_contains", path, False, str(exc)[:200])
        passed = text in content
        return CheckResult(
            "file_contains", path, passed, "contains text" if passed else "text not found"
        )

    return CheckResult(kind or "unknown", "", False, f"unknown check kind: {kind!r}")


def checks_summary(results: list[CheckResult]) -> str:
    """Human/LLM-readable summary fed back into the verifier prompt."""
    if not results:
        return "(no machine checks were provided)"
    lines = []
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"[{mark}] {r.kind} {r.target}: {r.evidence}")
    return "\n".join(lines)


def as_dicts(results: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
