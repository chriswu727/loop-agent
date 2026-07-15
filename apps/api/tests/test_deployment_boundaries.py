from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[3]


def _documents(path: str) -> list[dict[str, Any]]:
    return [
        document
        for document in yaml.safe_load_all((ROOT / path).read_text())
        if isinstance(document, dict)
    ]


def test_compose_provider_gateways_have_separate_credentials_and_proxy_only_networks() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    provider_definitions = {
        "email-gateway": (
            "loop-email-gateway",
            {"SMTP_HOST", "SMTP_PASSWORD", "IMAP_HOST"},
            {"CALDAV_URL", "CALDAV_PASSWORD", "PROVIDER_GATEWAY_GEMINI_API_KEY"},
        ),
        "calendar-gateway": (
            "loop-calendar-gateway",
            {"CALDAV_URL", "CALDAV_PASSWORD"},
            {"SMTP_HOST", "SMTP_PASSWORD", "IMAP_HOST", "PROVIDER_GATEWAY_GEMINI_API_KEY"},
        ),
        "vision-gateway": (
            "loop-vision-gateway",
            {"PROVIDER_GATEWAY_GEMINI_API_KEY"},
            {"SMTP_HOST", "SMTP_PASSWORD", "IMAP_HOST", "CALDAV_URL", "CALDAV_PASSWORD"},
        ),
    }

    for service_name, (
        audience,
        required_secrets,
        forbidden_secrets,
    ) in provider_definitions.items():
        gateway = services[service_name]
        environment = gateway["environment"]
        assert gateway["build"]["target"] == "runtime"
        assert environment["PROVIDER_GATEWAY_SERVICE_NAME"] == service_name
        assert environment["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == audience
        assert environment["PROVIDER_GATEWAY_BROWSER_ENABLED"] == "false"
        assert environment["PROVIDER_GATEWAY_EGRESS_PROXY_URL"] == "http://172.32.0.2:8080"
        assert environment["PROVIDER_GATEWAY_REQUIRE_SHARED_STATE"] == "true"
        assert environment["PROVIDER_GATEWAY_STATE_REDIS_URL"] == "redis://172.33.0.2:6379/1"
        assert required_secrets <= environment.keys()
        assert not forbidden_secrets & environment.keys()
        assert set(gateway["networks"]) == {"provider-egress", "enforcement-state"}
        assert gateway["dns"] == ["127.0.0.1"]

    worker_environment = services["worker"]["environment"]
    assert "AGENT_PROVIDER_GATEWAY_URL" not in worker_environment
    assert worker_environment["AGENT_EMAIL_GATEWAY_URL"] == "http://email-gateway:8090"
    assert worker_environment["AGENT_CALENDAR_GATEWAY_URL"] == "http://calendar-gateway:8090"
    assert worker_environment["AGENT_VISION_GATEWAY_URL"] == "http://vision-gateway:8090"
    assert compose["networks"]["provider-egress"]["internal"] is True
    assert compose["networks"]["enforcement-state"]["internal"] is True
    assert services["redis"]["networks"]["enforcement-state"]["ipv4_address"] == "172.33.0.2"
    assert services["egress-proxy"]["environment"]["EGRESS_PROXY_ALLOWED_PORTS"] == (
        "80,443,587,993"
    )
    assert services["egress-proxy"]["environment"]["EGRESS_PROXY_REQUIRE_SHARED_STATE"] == "true"


def test_compose_browser_has_no_credentials_or_direct_network() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    browser = services["browser-gateway"]

    browser_environment = browser["environment"]
    assert browser["build"]["target"] == "provider-gateway"
    assert browser_environment["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == ("loop-browser-gateway")
    assert browser_environment["PROVIDER_GATEWAY_EGRESS_PROXY_URL"] == ("http://172.31.0.2:8080")
    assert browser_environment["PROVIDER_GATEWAY_REQUIRE_SHARED_STATE"] == "true"
    assert browser_environment["PROVIDER_GATEWAY_STATE_REDIS_URL"] == ("redis://172.33.0.2:6379/1")
    assert (
        not {
            "SMTP_HOST",
            "SMTP_PASSWORD",
            "IMAP_HOST",
            "CALDAV_URL",
            "CALDAV_PASSWORD",
            "PROVIDER_GATEWAY_GEMINI_API_KEY",
        }
        & browser_environment.keys()
    )
    assert set(browser["networks"]) == {"browser-egress", "enforcement-state"}
    assert browser["dns"] == ["127.0.0.1"]
    assert compose["networks"]["browser-egress"]["internal"] is True
    assert services["worker"]["environment"]["AGENT_BROWSER_GATEWAY_URL"] == (
        "http://browser-gateway:8090"
    )


def test_kubernetes_browser_can_egress_only_to_proxy() -> None:
    browser = _documents("infra/k8s/base/browser-gateway-deployment.yaml")[0]
    policies = {
        document["metadata"]["name"]: document
        for document in _documents("infra/k8s/base/networkpolicy.yaml")
    }

    browser_spec = browser["spec"]["template"]["spec"]
    browser_container = browser_spec["containers"][0]
    browser_env = {item["name"]: item["value"] for item in browser_container["env"]}
    assert browser_spec["automountServiceAccountToken"] is False
    assert browser_spec["enableServiceLinks"] is True
    assert browser_spec["dnsPolicy"] == "None"
    assert browser_spec["dnsConfig"]["nameservers"] == ["127.0.0.1"]
    assert browser_container["image"] == "app-provider-gateway:latest"
    assert browser_container["envFrom"] == [{"secretRef": {"name": "browser-gateway-secrets"}}]
    assert browser_container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert browser_env["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == "loop-browser-gateway"
    assert browser_env["PROVIDER_GATEWAY_REQUIRE_SHARED_STATE"] == "true"
    assert "PROVIDER_GATEWAY_EGRESS_PROXY_URL" not in browser_env

    restriction = policies["restrict-browser-gateway-egress"]["spec"]
    assert restriction["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "browser-gateway"
    }
    assert restriction["policyTypes"] == ["Egress"]
    assert restriction["egress"] == [
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "egress-proxy"}}}],
            "ports": [{"protocol": "TCP", "port": 8080}],
        },
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "redis"}}}],
            "ports": [{"protocol": "TCP", "port": 6379}],
        },
    ]


def test_kubernetes_provider_gateways_can_egress_only_to_proxy() -> None:
    definitions = {
        "email-gateway": "loop-email-gateway",
        "calendar-gateway": "loop-calendar-gateway",
        "vision-gateway": "loop-vision-gateway",
    }
    for gateway_name, audience in definitions.items():
        deployment = _documents(f"infra/k8s/base/{gateway_name}-deployment.yaml")[0]
        spec = deployment["spec"]["template"]["spec"]
        container = spec["containers"][0]
        environment = {item["name"]: item["value"] for item in container["env"]}
        assert spec["automountServiceAccountToken"] is False
        assert spec["enableServiceLinks"] is True
        assert spec["dnsPolicy"] == "None"
        assert spec["dnsConfig"]["nameservers"] == ["127.0.0.1"]
        assert container["image"] == "app-api:latest"
        assert container["envFrom"] == [{"secretRef": {"name": f"{gateway_name}-secrets"}}]
        assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
        assert environment["PROVIDER_GATEWAY_SERVICE_NAME"] == gateway_name
        assert environment["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == audience
        assert environment["PROVIDER_GATEWAY_REQUIRE_SHARED_STATE"] == "true"
        assert environment["PROVIDER_GATEWAY_BROWSER_ENABLED"] == "false"
        assert "PROVIDER_GATEWAY_EGRESS_PROXY_URL" not in environment
        assert spec["volumes"] == [{"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}}]
        assert deployment["spec"].get("strategy", {}).get("type") != "Recreate"

    policies = {
        document["metadata"]["name"]: document
        for document in _documents("infra/k8s/base/networkpolicy.yaml")
    }
    restriction = policies["restrict-provider-gateways-egress"]["spec"]
    assert restriction["podSelector"]["matchLabels"] == {"app.kubernetes.io/component": "provider"}
    assert restriction["policyTypes"] == ["Egress"]
    assert restriction["egress"] == [
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "egress-proxy"}}}],
            "ports": [{"protocol": "TCP", "port": 8080}],
        },
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "redis"}}}],
            "ports": [{"protocol": "TCP", "port": 6379}],
        },
    ]


def test_kubernetes_egress_proxy_requires_shared_state_without_local_pvc() -> None:
    deployment = _documents("infra/k8s/base/egress-proxy-deployment.yaml")[0]
    spec = deployment["spec"]["template"]["spec"]
    container = spec["containers"][0]
    environment = {item["name"]: item["value"] for item in container["env"]}

    assert environment["EGRESS_PROXY_REQUIRE_SHARED_STATE"] == "true"
    assert environment["EGRESS_PROXY_REQUIRE_DURABLE_AUDIT"] == "true"
    assert environment["EGRESS_PROXY_REQUIRE_DURABLE_REVOCATIONS"] == "true"
    assert spec["enableServiceLinks"] is True
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert spec["volumes"] == [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}]
    assert deployment["spec"].get("strategy", {}).get("type") != "Recreate"


def test_kubernetes_provider_secrets_are_protocol_specific() -> None:
    secrets = {
        document["metadata"]["name"]: set(document["stringData"])
        for document in _documents("infra/k8s/base/secret.example.yaml")
    }
    verifier = {"PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY"}
    assert secrets["email-gateway-secrets"] == verifier | {
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "IMAP_HOST",
        "IMAP_PORT",
        "EMAIL_FROM",
    }
    assert secrets["calendar-gateway-secrets"] == verifier | {
        "CALDAV_URL",
        "CALDAV_USER",
        "CALDAV_PASSWORD",
    }
    assert secrets["vision-gateway-secrets"] == verifier | {"PROVIDER_GATEWAY_GEMINI_API_KEY"}


def test_ci_runs_real_redis_enforcement_acceptance() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["enforcement-acceptance"]

    assert job["name"] == "enforcement · real Redis restart + cross-process revoke"
    assert any(
        step.get("run") == "bash ../../scripts/enforcement-acceptance.sh" for step in job["steps"]
    )


def test_ci_runs_disposable_kubernetes_acceptance() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["kubernetes-acceptance"]

    assert job["name"] == "kubernetes · deploy + task + rollback"
    assert job["timeout-minutes"] == 25
    assert any(
        step.get("run") == "bash scripts/k8s-deployment-acceptance.sh" for step in job["steps"]
    )
    install = next(step["run"] for step in job["steps"] if step.get("name") == "Install k3d")
    assert "v5.9.0/k3d-linux-amd64" in install
    assert "06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0" in install


def test_kubernetes_acceptance_is_production_mode_with_ephemeral_dependencies() -> None:
    overlay = yaml.safe_load(
        (ROOT / "infra/k8s/overlays/acceptance/kustomization.yaml").read_text()
    )
    config = _documents("infra/k8s/overlays/acceptance/patch-config.yaml")[0]["data"]
    dependencies = _documents("infra/k8s/overlays/acceptance/dependencies.yaml")
    resources = {(document["kind"], document["metadata"]["name"]) for document in dependencies}

    assert overlay["namespace"] == "loop-acceptance"
    assert overlay["resources"] == ["../../base", "dependencies.yaml"]
    assert config["ENVIRONMENT"] == "production"
    assert config["DEMO_MODE"] == "true"
    assert config["LLM_DEFAULT_PROVIDER"] == "mock"
    assert config["AGENT_SANDBOX_IMAGE"] == "loop-sandbox:acceptance"
    assert config["AGENT_SANDBOX_IMAGE_DIGEST"] == "sha256:" + "0" * 64
    assert ("Deployment", "postgres") in resources
    assert ("Deployment", "redis") in resources
    assert ("NetworkPolicy", "allow-postgres-from-loop-runtimes") in resources


def test_kubernetes_acceptance_migrates_runs_task_and_rolls_back() -> None:
    migration = _documents("infra/k8s/overlays/acceptance/migration-job.yaml")[0]
    script = (ROOT / "scripts/k8s-deployment-acceptance.sh").read_text()
    smoke = (ROOT / "scripts/k8s-enforcement-smoke.sh").read_text()

    container = migration["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "loop-api:acceptance"
    assert container["imagePullPolicy"] == "Never"
    assert container["command"] == ["alembic", "upgrade", "head"]
    assert "run_cluster_probe before-rollback true" in script
    assert "kubectl rollout undo deployment/api" in script
    assert 'task["sandbox"] != "kubernetes"' in script
    assert 'report.get("authentic")' in script
    assert "0006_authority_audit" in script
    assert "api web worker" in smoke
    assert 'cluster="${LOOP_ACCEPTANCE_CLUSTER:-la-' in script
    assert len("la-29386447741-1-2695") <= 32
