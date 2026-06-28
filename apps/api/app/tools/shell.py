"""Run a shell command inside the workspace, bounded by a timeout and an output cap.

Commands run with ``cwd`` set to the workspace so relative work stays local, and
their combined stdout/stderr is truncated so a chatty command can't blow up the
agent's context or the token budget.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.tools.base import ToolResult, ToolStatus


async def run_command(
    command: str,
    cwd: Path,
    *,
    timeout_seconds: int = 60,
    output_limit: int = 4000,
) -> ToolResult:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:  # spawn failure (bad shell, etc.)
        return ToolResult(f"Failed to start command: {exc}", ToolStatus.ERROR)

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult(
            f"Command timed out after {timeout_seconds}s and was killed.",
            ToolStatus.ERROR,
        )

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    if len(output) > output_limit:
        output = output[:output_limit] + f"\n... [truncated, {len(output)} chars total]"

    code = proc.returncode
    header = f"exit code {code}\n"
    status = ToolStatus.OK if code == 0 else ToolStatus.ERROR
    return ToolResult(header + (output or "(no output)"), status)
