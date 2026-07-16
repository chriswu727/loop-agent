"""Run a shell command inside the workspace, bounded by a timeout and an output cap.

Commands run from the workspace with a scrubbed environment (never the API
process's secrets), their combined stdout/stderr is drained with a hard byte cap
so a chatty command can't exhaust host memory, and they run in their own process
group so a timeout kills the whole tree, not just the shell.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal

from app.tools.base import ToolResult, ToolStatus

# Only these reach a command — so an allowlisted `env`/`printenv` can't leak the
# API process's secrets (LLM keys, DB URL, tokens) into the observation/ledger.
_SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "TMPDIR",
    "PYTHONUNBUFFERED",
)
_READ_CAP_MULT = 4  # bytes kept before a runaway-output command is killed


def empty_test_suite_reason(command: str, observation: str) -> str | None:
    if re.search(r"(?mi)^Ran 0 tests?\b", observation):
        return "the test runner executed zero tests"
    runner = re.search(
        r"(?i)(?:^|[\s;&|])(?:pytest|py\.test|vitest|jest|mocha)(?:[\s;&|]|$)"
        r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:[\s;&|]|$)",
        command,
    )
    if runner and re.search(
        r"(?i)\b(?:no tests ran|collected 0 items|no tests found|no test files found|0 passing)\b",
        observation,
    ):
        return "the test runner reported an empty test suite"
    return None


def _safe_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the command and any descendants (it's a session leader), so a timeout
    or overflow doesn't orphan child processes."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


async def collect_output(
    proc: asyncio.subprocess.Process, *, timeout_seconds: int, output_limit: int
) -> tuple[bytes | None, int | None]:
    """Drain stdout under a wall-clock timeout with a hard byte cap — killing the
    process (group) on overflow so host memory is bounded, not just a container's.
    Returns (raw_output, exit_code), or (None, None) if it timed out."""
    cap = max(output_limit * _READ_CAP_MULT, output_limit + 4096)

    async def drain() -> bytes:
        data = bytearray()
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > cap:
                kill_process_group(proc)  # runaway output -> stop reading
                break
        return bytes(data)

    try:
        raw = await asyncio.wait_for(drain(), timeout=timeout_seconds)
    except TimeoutError:
        kill_process_group(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        return None, None
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)
    return raw, proc.returncode


def format_result(
    raw: bytes | None, code: int | None, *, timeout_seconds: int, output_limit: int
) -> ToolResult:
    if raw is None:
        return ToolResult(
            f"Command timed out after {timeout_seconds}s and was killed.", ToolStatus.ERROR
        )
    output = raw.decode("utf-8", errors="replace")
    if len(output) > output_limit:
        output = output[:output_limit] + f"\n... [truncated, {len(output)} chars total]"
    status = ToolStatus.OK if code == 0 else ToolStatus.ERROR
    return ToolResult(f"exit code {code}\n" + (output or "(no output)"), status)


async def run_command(
    command: str,
    cwd: object,
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
            env=_safe_env(),
            start_new_session=True,  # own process group -> kill the whole tree
        )
    except Exception as exc:  # spawn failure (bad shell, etc.)
        return ToolResult(f"Failed to start command: {exc}", ToolStatus.ERROR)

    raw, code = await collect_output(
        proc, timeout_seconds=timeout_seconds, output_limit=output_limit
    )
    result = format_result(raw, code, timeout_seconds=timeout_seconds, output_limit=output_limit)
    empty_suite = empty_test_suite_reason(command, result.observation)
    if empty_suite is not None:
        return ToolResult(
            result.observation
            + "\n[INVALID TEST RUN: zero tests were executed. This is not evidence that the "
            "source file was truncated. For unittest discovery, put TestCase classes in a "
            "test_*.py file; for other runners, add a discoverable test file.]",
            ToolStatus.ERROR,
        )
    return result
