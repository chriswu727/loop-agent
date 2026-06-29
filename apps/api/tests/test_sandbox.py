"""Sandbox routing: run_command goes to the container when an image is set, and
the container's network is granted only when the envelope allows egress. (The
real container execution is exercised by a live run; here we test the wiring
offline by stubbing the container runner.)"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools import CapabilityEnvelope, ToolExecutor, ToolResult, ToolStatus, Workspace
from app.tools.sandbox import image_present


async def test_run_command_routes_to_container_when_image_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def fake_container(command: str, root: Path, *, image: str, network: bool, **_: object):
        seen.update(command=command, image=image, network=network)
        return ToolResult("exit code 0\nok", ToolStatus.OK)

    monkeypatch.setattr("app.tools.registry.run_command_in_container", fake_container)

    # Egress not allowed -> the container gets no network.
    env = CapabilityEnvelope.from_tools(None, egress_allowed=False)
    ex = ToolExecutor(Workspace(tmp_path / "w"), envelope=env, sandbox_image="loop-sandbox:latest")
    res = await ex.execute("run_command", {"command": "echo hi"})
    assert res.status is ToolStatus.OK
    assert seen == {"command": "echo hi", "image": "loop-sandbox:latest", "network": False}


async def test_sandbox_network_follows_egress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def fake_container(command: str, root: Path, *, image: str, network: bool, **_: object):
        seen["network"] = network
        return ToolResult("exit code 0\n", ToolStatus.OK)

    monkeypatch.setattr("app.tools.registry.run_command_in_container", fake_container)
    env = CapabilityEnvelope.from_tools(None, egress_allowed=True)
    ex = ToolExecutor(Workspace(tmp_path / "w"), envelope=env, sandbox_image="img")
    await ex.execute("run_command", {"command": "curl x"})
    assert seen["network"] is True  # egress allowed -> container has network


async def test_no_sandbox_uses_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    used = {"host": False}

    async def fake_host(command: str, cwd: Path, **_: object):
        used["host"] = True
        return ToolResult("exit code 0\n", ToolStatus.OK)

    monkeypatch.setattr("app.tools.registry.run_command", fake_host)
    ex = ToolExecutor(Workspace(tmp_path / "w"))  # no sandbox_image
    await ex.execute("run_command", {"command": "echo hi"})
    assert used["host"] is True


def test_image_present_false_for_missing() -> None:
    assert image_present("loop-definitely-not-real:nope") is False
