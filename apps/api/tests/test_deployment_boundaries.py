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


def test_compose_browser_has_no_credentials_or_direct_network() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    protocol = services["provider-gateway"]
    browser = services["browser-gateway"]

    assert protocol["build"]["target"] == "runtime"
    assert protocol["environment"]["PROVIDER_GATEWAY_BROWSER_ENABLED"] == "false"
    assert "PROVIDER_GATEWAY_EGRESS_PROXY_URL" not in protocol["environment"]

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
    provider = _documents("infra/k8s/base/provider-gateway-deployment.yaml")[0]
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

    provider_container = provider["spec"]["template"]["spec"]["containers"][0]
    provider_env = {item["name"]: item["value"] for item in provider_container["env"]}
    assert provider_container["image"] == "app-api:latest"
    assert provider_env["PROVIDER_GATEWAY_BROWSER_ENABLED"] == "false"
    assert "PROVIDER_GATEWAY_EGRESS_PROXY_URL" not in provider_env

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
