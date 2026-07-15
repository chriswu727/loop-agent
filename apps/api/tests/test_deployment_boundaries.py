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
        assert required_secrets <= environment.keys()
        assert not forbidden_secrets & environment.keys()
        assert set(gateway["networks"]) == {"provider-egress"}
        assert gateway["dns"] == ["127.0.0.1"]

    worker_environment = services["worker"]["environment"]
    assert "AGENT_PROVIDER_GATEWAY_URL" not in worker_environment
    assert worker_environment["AGENT_EMAIL_GATEWAY_URL"] == "http://email-gateway:8090"
    assert worker_environment["AGENT_CALENDAR_GATEWAY_URL"] == "http://calendar-gateway:8090"
    assert worker_environment["AGENT_VISION_GATEWAY_URL"] == "http://vision-gateway:8090"
    assert compose["networks"]["provider-egress"]["internal"] is True
    assert services["egress-proxy"]["environment"]["EGRESS_PROXY_ALLOWED_PORTS"] == (
        "80,443,587,993"
    )


def test_compose_browser_has_no_credentials_or_direct_network() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    browser = services["browser-gateway"]

    browser_environment = browser["environment"]
    assert browser["build"]["target"] == "provider-gateway"
    assert browser_environment["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == ("loop-browser-gateway")
    assert browser_environment["PROVIDER_GATEWAY_EGRESS_PROXY_URL"] == ("http://172.31.0.2:8080")
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
    assert set(browser["networks"]) == {"browser-egress"}
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
    assert browser_env["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == "loop-browser-gateway"
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
        }
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
        assert environment["PROVIDER_GATEWAY_SERVICE_NAME"] == gateway_name
        assert environment["PROVIDER_GATEWAY_AUTHORITY_AUDIENCE"] == audience
        assert environment["PROVIDER_GATEWAY_BROWSER_ENABLED"] == "false"
        assert "PROVIDER_GATEWAY_EGRESS_PROXY_URL" not in environment
        assert spec["volumes"][1]["persistentVolumeClaim"]["claimName"] == (f"{gateway_name}-state")

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
        }
    ]


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
