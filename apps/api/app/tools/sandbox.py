"""Run a shell command inside an ephemeral Docker container.

This is the jailed execution path: the command runs in a throwaway container with
only the task workspace bind-mounted, a read-only rootfs, capped memory/CPU/pids,
and — unless egress is granted — no network at all. So a command can read and
write the workspace but cannot reach the host filesystem or the network. The
container is removed on exit (``--rm``) and force-removed if it overruns.
"""

from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache
from pathlib import Path

from app.core.logging import get_logger
from app.tools.base import ToolResult, ToolStatus

log = get_logger("sandbox")


@lru_cache(maxsize=1)
def docker_available() -> bool:
    """Whether a Docker daemon is reachable. Cached for the process lifetime."""
    import subprocess

    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        ).returncode == 0
    except Exception:
        return False


def image_present(image: str) -> bool:
    import subprocess

    try:
        return subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=10
        ).returncode == 0
    except Exception:
        return False


async def run_command_in_container(
    command: str,
    workspace_root: Path,
    *,
    image: str,
    network: bool,
    timeout_seconds: int = 60,
    output_limit: int = 4000,
    memory: str = "512m",
    cpus: str = "1",
) -> ToolResult:
    name = f"loop-{uuid.uuid4().hex[:12]}"
    argv = [
        "docker", "run", "--rm", "--name", name,
        "--network", "bridge" if network else "none",
        "--memory", memory, "--memory-swap", memory, "--cpus", cpus, "--pids-limit", "256",
        "--read-only", "--tmpfs", "/tmp:rw,size=64m,exec",
        "-v", f"{workspace_root}:/workspace",
        "-w", "/workspace",
        image,
        "sh", "-lc", command,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except Exception as exc:
        return ToolResult(f"Failed to start sandbox: {exc}", ToolStatus.ERROR)

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds + 15)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        await _force_remove(name)
        return ToolResult(
            f"Command timed out after {timeout_seconds}s and was killed.", ToolStatus.ERROR
        )

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    if len(output) > output_limit:
        output = output[:output_limit] + f"\n... [truncated, {len(output)} chars total]"
    code = proc.returncode
    status = ToolStatus.OK if code == 0 else ToolStatus.ERROR
    return ToolResult(f"exit code {code}\n" + (output or "(no output)"), status)


async def _force_remove(name: str) -> None:
    try:
        rm = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await rm.wait()
    except Exception:
        log.warning("sandbox.force_remove_failed", name=name)
