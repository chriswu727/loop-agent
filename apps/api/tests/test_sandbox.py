"""Sandbox routing: run_command goes to the container when an image is set, and
the container's network is granted only when the envelope allows egress. (The
real container execution is exercised by a live run; here we test the wiring
offline by stubbing the container runner.)"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools import CapabilityEnvelope, ToolExecutor, ToolResult, ToolStatus, Workspace
from app.tools.egress import resolve_proxy_endpoint
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

    async def fake_container(
        command: str,
        root: Path,
        *,
        image: str,
        network: bool,
        egress_proxy_url: str | None,
        egress_token: str | None,
        egress_network: str | None,
        **_: object,
    ):
        seen.update(
            network=network,
            proxy=egress_proxy_url,
            token=egress_token,
            network_name=egress_network,
        )
        return ToolResult("exit code 0\n", ToolStatus.OK)

    monkeypatch.setattr("app.tools.registry.run_command_in_container", fake_container)
    env = CapabilityEnvelope.from_tools(None, egress_allowed=True, egress_hosts=["example.com"])
    ex = ToolExecutor(
        Workspace(tmp_path / "w"),
        envelope=env,
        sandbox_image="img",
        egress_proxy_url="http://egress-proxy:8080",
        egress_network="loop_sandbox-egress",
        egress_token_factory=lambda: "short-lived-token",
    )
    await ex.execute("run_command", {"command": "curl x"})
    assert seen == {
        "network": True,
        "proxy": "http://egress-proxy:8080",
        "token": "short-lived-token",
        "network_name": "loop_sandbox-egress",
    }


async def test_no_sandbox_uses_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    used = {"host": False}

    async def fake_host(command: str, cwd: Path, **_: object):
        used["host"] = True
        return ToolResult("exit code 0\n", ToolStatus.OK)

    monkeypatch.setattr("app.tools.registry.run_command", fake_host)
    ex = ToolExecutor(
        Workspace(tmp_path / "w"),
        envelope=CapabilityEnvelope.from_capabilities(["exec"]),
    )
    await ex.execute("run_command", {"command": "echo hi"})
    assert used["host"] is True


def test_image_present_false_for_missing() -> None:
    assert image_present("loop-definitely-not-real:nope") is False


def test_kubernetes_sandbox_mounts_only_a_task_subpath(tmp_path: Path) -> None:
    from app.tools.kubernetes_sandbox import _workspace_subpath

    mount = tmp_path / "data"
    workspace = mount / "workspaces" / "task-id"
    workspace.mkdir(parents=True)

    assert _workspace_subpath(mount, workspace) == "workspaces/task-id"
    assert _workspace_subpath(mount, mount) is None
    assert _workspace_subpath(mount, tmp_path / "other") is None


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("512m", "512Mi"),
        ("1g", "1Gi"),
        ("1024k", "1024Ki"),
        ("4096b", "4096"),
        ("768Mi", "768Mi"),
    ],
)
def test_kubernetes_sandbox_normalizes_docker_memory_units(configured: str, expected: str) -> None:
    from app.tools.kubernetes_sandbox import _kubernetes_memory_quantity

    assert _kubernetes_memory_quantity(configured) == expected


def test_docker_sandbox_mounts_only_a_task_volume_subpath(tmp_path: Path) -> None:
    from app.tools.sandbox import _workspace_volume_subpath

    mount = tmp_path / "data"
    workspace = mount / "workspaces" / "task-id"
    workspace.mkdir(parents=True)

    assert _workspace_volume_subpath(mount, workspace) == "workspaces/task-id"
    assert _workspace_volume_subpath(mount, mount) is None
    assert _workspace_volume_subpath(mount, tmp_path / "other") is None


async def test_proxy_endpoint_is_resolved_before_entering_the_sandbox() -> None:
    async def resolver(host: str, port: int) -> str:
        assert (host, port) == ("egress-proxy.loop.svc", 8080)
        return "10.96.4.20"

    assert (
        await resolve_proxy_endpoint("http://egress-proxy.loop.svc:8080", resolver=resolver)
        == "http://10.96.4.20:8080"
    )


async def test_networked_docker_sandbox_disables_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.tools.sandbox as sandbox

    seen: dict[str, tuple[str, ...]] = {}

    async def fake_create(*argv: str, **_kwargs: object) -> object:
        seen["argv"] = argv
        return object()

    async def fake_collect(
        _proc: object, *, timeout_seconds: int, output_limit: int
    ) -> tuple[bytes, int]:
        assert timeout_seconds == 75
        assert output_limit == 4000
        return b"ok", 0

    async def fake_remove(_name: str) -> None:
        return None

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(sandbox, "collect_output", fake_collect)
    monkeypatch.setattr(sandbox, "_force_remove", fake_remove)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = await sandbox.run_command_in_container(
        "curl https://allowed.example",
        workspace,
        image="sandbox:latest",
        network=True,
        egress_proxy_url="http://172.30.0.2:8080",
        egress_token="short-token",
        egress_network="loop_sandbox-egress",
    )

    assert result.status is ToolStatus.OK
    argv = seen["argv"]
    assert argv[argv.index("--dns") + 1] == "127.0.0.1"
    assert any(
        value.startswith("HTTP_PROXY=http://loop:short-token@172.30.0.2:8080") for value in argv
    )


def test_docker_available_does_not_cache_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tools.sandbox as sb

    sb._docker_confirmed = False
    calls = {"n": 0}

    class _R:
        returncode = 1

    def fake_run(*a: object, **k: object) -> _R:
        calls["n"] += 1
        return _R()

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    assert sb.docker_available() is False
    assert sb.docker_available() is False
    assert calls["n"] == 2  # negative not cached -> re-checked (Docker may start later)


def test_docker_available_caches_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tools.sandbox as sb

    sb._docker_confirmed = False
    calls = {"n": 0}

    class _R:
        returncode = 0

    def fake_run(*a: object, **k: object) -> _R:
        calls["n"] += 1
        return _R()

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    assert sb.docker_available() is True
    assert sb.docker_available() is True
    assert calls["n"] == 1  # positive cached -> no repeat docker info
    sb._docker_confirmed = False  # reset so other tests re-detect real Docker
