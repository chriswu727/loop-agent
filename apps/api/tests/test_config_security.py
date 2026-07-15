from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from pydantic import ValidationError

from app.core.config import Settings

TEST_RECEIPT_KEY = (
    Ed25519PrivateKey.generate()
    .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    .decode()
)


def production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "OTEL_SERVICE_NAME": "worker",
        "auth_required": True,
        "secret_key": "a-production-secret-that-is-at-least-32-bytes",
        "agent_sandbox": "required",
        "agent_sandbox_image_digest": "sha256:" + "a" * 64,
        "agent_allow_host_providers": False,
        "agent_receipt_signing_key": TEST_RECEIPT_KEY,
        "agent_authority_signing_key": TEST_RECEIPT_KEY,
        "agent_provider_gateway_url": None,
        "agent_browser_gateway_url": "http://browser-gateway:8090",
        "agent_email_gateway_url": "http://email-gateway:8090",
        "agent_calendar_gateway_url": "http://calendar-gateway:8090",
        "agent_vision_gateway_url": "http://vision-gateway:8090",
        "agent_email_egress_hosts": "smtp.example.com,imap.example.com",
        "agent_calendar_egress_hosts": "caldav.example.com",
        "agent_vision_egress_hosts": "generativelanguage.googleapis.com",
        "agent_egress_proxy_url": "http://egress-proxy:8080",
        "agent_egress_proxy_audit_url": "http://egress-proxy:8081",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_secure_production_settings_are_accepted() -> None:
    assert production().is_production is True


@pytest.mark.parametrize(
    "override",
    [
        {"auth_required": False},
        {"secret_key": "short"},
        {"secret_key": "CHANGE_ME_shared_session_secret_at_least_32_bytes"},
        {"agent_sandbox": "preferred"},
        {"agent_sandbox_image_digest": None},
        {"agent_allow_host_providers": True},
        {"agent_provider_gateway_url": "http://provider-gateway:8090"},
        {"agent_browser_gateway_url": None},
        {"agent_email_gateway_url": None},
        {"agent_calendar_gateway_url": None},
        {"agent_vision_gateway_url": None},
        {"agent_email_egress_hosts": ""},
        {"agent_email_egress_hosts": "   "},
        {"agent_calendar_egress_hosts": ""},
        {"agent_vision_egress_hosts": ""},
        {"agent_egress_proxy_url": None},
        {"agent_egress_proxy_audit_url": None},
        {"agent_authority_signing_key": None},
        {"agent_authority_signing_key": "not-a-key"},
        {"agent_receipt_signing_key": None},
        {"agent_receipt_signing_key": "not-a-key"},
    ],
)
def test_insecure_production_settings_fail_fast(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        production(**override)


def test_sandbox_digest_must_be_a_sha256_reference() -> None:
    with pytest.raises(ValidationError):
        production(agent_sandbox_image_digest="latest")

    digest = "sha256:" + "a" * 64
    assert production(agent_sandbox_image_digest=digest).agent_sandbox_image_digest == digest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://app.example.com", ["https://app.example.com"]),
        (
            "https://app.example.com,https://admin.example.com",
            ["https://app.example.com", "https://admin.example.com"],
        ),
        ('["https://app.example.com"]', ["https://app.example.com"]),
    ],
)
def test_cors_origins_parse_environment_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", raw)

    assert Settings(_env_file=None).cors_origins == expected
