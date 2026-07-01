"""Run a shell command inside an ephemeral Docker container.

This is the jailed execution path: the command runs in a throwaway container with
only the task workspace bind-mounted, a read-only rootfs, capped memory/CPU/pids,
and — unless egress is granted — no network at all. So a command can read and
write the workspace but cannot reach the host filesystem or the network. The
container is removed on exit (``--rm``) and force-removed if it overruns.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

from app.core.logging import get_logger
from app.tools.base import ToolResult, ToolStatus

log = get_logger("sandbox")

# Cache only a *positive* result: once the daemon is confirmed up it stays up, so
# we skip the ~0.5s `docker info` on later tasks. A negative result is NOT cached,
# so starting Docker after the app boots is picked up on the next task (the old
# lru_cache pinned "unavailable" for the whole process — a real footgun).
_docker_confirmed = False


def docker_available() -> bool:
    """Whether a Docker daemon is reachable right now."""
    global _docker_confirmed
    if _docker_confirmed:
        return True
    try:
        ok = subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        ok = False
    _docker_confirmed = ok
    return ok


def image_present(image: str) -> bool:
    import subprocess

    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=10
            ).returncode
            == 0
        )
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
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "bridge" if network else "none",
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        cpus,
        "--pids-limit",
        "256",
        # Drop every capability, forbid regaining privileges via setuid binaries,
        # run as the image's unprivileged uid, and never pull from a registry (we
        # already checked the image is present — fail fast instead of a 75s stall).
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "10001:10001",
        "--pull",
        "never",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m,exec",
        "-v",
        f"{workspace_root}:/workspace",
        "-w",
        "/workspace",
        image,
        "sh",
        "-lc",
        command,
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
            "docker",
            "rm",
            "-f",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await rm.wait()
    except Exception:
        log.warning("sandbox.force_remove_failed", name=name)
