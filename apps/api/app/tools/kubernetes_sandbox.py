"""Run one command in a short-lived, locked-down Kubernetes Job."""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from pathlib import Path
from typing import Any, cast

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import ToolResult, ToolStatus
from app.tools.egress import authenticated_proxy_url, resolve_proxy_endpoint
from app.tools.shell import format_result

log = get_logger("kubernetes-sandbox")


def _kubernetes_memory_quantity(memory: str) -> str:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([bkmg])", memory, flags=re.IGNORECASE)
    if match is None:
        return memory
    value, unit = match.groups()
    suffix = {"b": "", "k": "Ki", "m": "Mi", "g": "Gi"}[unit.lower()]
    return f"{value}{suffix}"


def _workspace_subpath(mount: Path, workspace: Path) -> str | None:
    try:
        relative = workspace.resolve().relative_to(mount.resolve())
    except ValueError:
        return None
    return None if relative == Path(".") else relative.as_posix()


def _resolve_workspace_scope(workspace_root: Path) -> tuple[Path, Path, str | None]:
    mount = Path(settings.agent_kubernetes_data_mount).resolve()
    workspace = workspace_root.resolve()
    return mount, workspace, _workspace_subpath(mount, workspace)


async def run_command_in_kubernetes(
    command: str,
    workspace_root: Path,
    *,
    image: str,
    network: bool,
    egress_proxy_url: str | None = None,
    egress_token: str | None = None,
    timeout_seconds: int,
    output_limit: int,
    memory: str,
    cpus: str,
) -> ToolResult:
    if network and (not egress_proxy_url or not egress_token):
        return ToolResult(
            "Network authority requires the destination-enforcing egress proxy.",
            ToolStatus.BLOCKED,
        )
    resolved_proxy_url: str | None = None
    if network:
        try:
            resolved_proxy_url = await resolve_proxy_endpoint(egress_proxy_url or "")
        except (OSError, ValueError) as exc:
            return ToolResult(f"Egress proxy is unavailable: {exc}", ToolStatus.BLOCKED)
    mount, workspace, subpath = _resolve_workspace_scope(workspace_root)
    if subpath is None:
        return ToolResult(
            f"Workspace {workspace} is not an isolated child of Kubernetes data mount {mount}.",
            ToolStatus.BLOCKED,
        )

    from kubernetes_asyncio import client, config

    try:
        config.load_incluster_config()  # type: ignore[no-untyped-call]
    except config.ConfigException as exc:
        return ToolResult(f"Kubernetes sandbox is unavailable: {exc}", ToolStatus.ERROR)

    namespace = settings.agent_kubernetes_namespace
    name = f"loop-sandbox-{uuid.uuid4().hex[:12]}"
    memory_quantity = _kubernetes_memory_quantity(memory)
    labels = {
        "app.kubernetes.io/name": "loop-sandbox",
        "app.kubernetes.io/component": "sandbox",
        "loop.openai.com/egress": "proxied" if network else "denied",
    }
    body: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": timeout_seconds + 15,
            "ttlSecondsAfterFinished": 60,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "command",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["sh", "-lc", command],
                            "workingDir": "/workspace",
                            "resources": {
                                "requests": {"cpu": cpus, "memory": memory_quantity},
                                "limits": {"cpu": cpus, "memory": memory_quantity},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "env": (
                                [
                                    {
                                        "name": "HTTP_PROXY",
                                        "value": authenticated_proxy_url(
                                            resolved_proxy_url or "", egress_token or ""
                                        ),
                                    },
                                    {
                                        "name": "HTTPS_PROXY",
                                        "value": authenticated_proxy_url(
                                            resolved_proxy_url or "", egress_token or ""
                                        ),
                                    },
                                    {"name": "NO_PROXY", "value": ""},
                                ]
                                if network
                                else []
                            ),
                            "volumeMounts": [
                                {
                                    "name": "data",
                                    "mountPath": "/workspace",
                                    "subPath": subpath,
                                },
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "data",
                            "persistentVolumeClaim": {
                                "claimName": settings.agent_kubernetes_data_pvc
                            },
                        },
                        {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                    ],
                },
            },
        },
    }
    api_client = client.ApiClient()
    batch = client.BatchV1Api(api_client)
    core = client.CoreV1Api(api_client)
    try:
        await batch.create_namespaced_job(namespace=namespace, body=cast(Any, body))
        try:
            await asyncio.wait_for(
                _wait_for_job(batch, namespace, name), timeout=timeout_seconds + 20
            )
        except TimeoutError:
            return format_result(
                None, None, timeout_seconds=timeout_seconds, output_limit=output_limit
            )
        pods = await core.list_namespaced_pod(
            namespace=namespace, label_selector=f"job-name={name}"
        )
        if not pods.items:
            return ToolResult("Sandbox Job finished without a pod record.", ToolStatus.ERROR)
        pod = pods.items[0]
        log_text = await core.read_namespaced_pod_log(name=pod.metadata.name, namespace=namespace)
        state = pod.status.container_statuses[0].state.terminated
        code = int(state.exit_code) if state is not None else 1
        return format_result(
            str(log_text).encode(),
            code,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
        )
    except Exception as exc:
        log.warning("sandbox.job_failed", job=name, error=str(exc)[:300])
        return ToolResult(f"Kubernetes sandbox failed: {exc}", ToolStatus.ERROR)
    finally:
        with contextlib.suppress(Exception):
            await batch.delete_namespaced_job(
                name=name,
                namespace=namespace,
                propagation_policy="Background",
            )
        await api_client.close()


async def _wait_for_job(batch: Any, namespace: str, name: str) -> None:
    while True:
        job = await batch.read_namespaced_job_status(name=name, namespace=namespace)
        if job.status.succeeded or job.status.failed:
            return
        await asyncio.sleep(0.5)
