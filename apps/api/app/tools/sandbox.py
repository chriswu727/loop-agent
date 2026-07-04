"""Run a shell command inside an ephemeral Docker container.

This is the jailed execution path: the command runs in a throwaway container with
only the task workspace bind-mounted, a read-only rootfs, capped memory/CPU/pids,
and — unless egress is granted — no network at all. So a command can read and
write the workspace but cannot reach the host filesystem or the network. The
container is removed on exit (``--rm``) and force-removed if it overruns.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import uuid
from pathlib import Path

from app.core.logging import get_logger
from app.tools.base import ToolResult, ToolStatus
from app.tools.shell import collect_output, format_result

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
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Own session/process group: collect_output kills the group on overflow
            # or timeout, and without this the docker client shares the API's group,
            # so the kill would SIGKILL uvicorn and every concurrent task.
            start_new_session=True,
        )
    except Exception as exc:
        return ToolResult(f"Failed to start sandbox: {exc}", ToolStatus.ERROR)

    try:
        # Same byte-capped drain as the host path, so a container spewing output
        # can't exhaust host memory even though its own memory is capped.
        raw, code = await collect_output(
            proc, timeout_seconds=timeout_seconds + 15, output_limit=output_limit
        )
    finally:
        # Guarantee the container is gone (killing the client on timeout/overflow
        # can orphan it; --rm only cleans up a clean exit).
        await _force_remove(name)
    return format_result(raw, code, timeout_seconds=timeout_seconds, output_limit=output_limit)


async def _force_remove(name: str) -> None:
    # Runs in the finally of every sandbox command. Bound the wait so a hung Docker
    # daemon can't wedge the agent coroutine (and leak its DB session) forever.
    try:
        rm = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(rm.wait(), timeout=15)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                rm.kill()
            log.warning("sandbox.force_remove_timeout", name=name)
    except Exception:
        log.warning("sandbox.force_remove_failed", name=name)
