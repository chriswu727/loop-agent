from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.domain.authority_token import (
    EGRESS_PROXY_AUDIENCE,
    PROVIDER_GATEWAY_AUDIENCE,
    AuthorityTokenError,
    intersect_host_policies,
    issue_authority_token,
    normalize_host,
    public_key_pem,
    validate_authority_public_key,
    verify_authority_token,
)
from app.domain.capability import Capability


@pytest.fixture
def authority_keys() -> tuple[str, str]:
    private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    return private, public_key_pem(private)


def test_authority_token_round_trip_is_task_and_capability_scoped(
    authority_keys: tuple[str, str],
) -> None:
    private, public = authority_keys
    token = issue_authority_token(
        private,
        audience=PROVIDER_GATEWAY_AUDIENCE,
        task_id="task-1",
        owner_id="owner-1",
        project_id="project-1",
        run_id="task-1:1",
        capabilities=[Capability.EMAIL_READ, Capability.NET_BROWSER],
        egress_hosts=["Example.COM", "api.github.com."],
        ttl_seconds=120,
    )
    grant = verify_authority_token(token, public, audience=PROVIDER_GATEWAY_AUDIENCE)
    assert grant.task_id == "task-1"
    assert grant.owner_id == "owner-1"
    assert grant.project_id == "project-1"
    assert grant.run_id == "task-1:1"
    assert grant.capabilities == frozenset({Capability.EMAIL_READ, Capability.NET_BROWSER})
    assert grant.permits_host("www.example.com")
    assert not grant.permits_host("example.com.evil.test")


def test_authority_token_rejects_wrong_audience_and_tampering(
    authority_keys: tuple[str, str],
) -> None:
    private, public = authority_keys
    token = issue_authority_token(
        private,
        audience=PROVIDER_GATEWAY_AUDIENCE,
        task_id="task-1",
        owner_id="owner-1",
        project_id="project-1",
        run_id="task-1:1",
        capabilities=[Capability.EMAIL_READ],
        egress_hosts=[],
        ttl_seconds=120,
    )
    with pytest.raises(AuthorityTokenError):
        verify_authority_token(token, public, audience=EGRESS_PROXY_AUDIENCE)
    header, payload, signature = token.split(".")
    corrupted_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    corrupted = ".".join((header, payload, corrupted_signature))
    with pytest.raises(AuthorityTokenError):
        verify_authority_token(corrupted, public, audience=PROVIDER_GATEWAY_AUDIENCE)


def test_authority_token_expiry_is_enforced(authority_keys: tuple[str, str]) -> None:
    private, public = authority_keys
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    token = issue_authority_token(
        private,
        audience=EGRESS_PROXY_AUDIENCE,
        task_id="task-1",
        owner_id="owner-1",
        project_id="project-1",
        run_id="task-1:1",
        capabilities=[Capability.NET_SHELL],
        egress_hosts=["example.com"],
        ttl_seconds=30,
        now=issued,
    )
    with pytest.raises(AuthorityTokenError, match="not currently valid"):
        verify_authority_token(
            token,
            public,
            audience=EGRESS_PROXY_AUDIENCE,
            now=issued + timedelta(seconds=31),
        )


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "http://example.com",
        "*.example.com",
        "example.com/path",
    ],
)
def test_egress_host_normalization_rejects_private_or_ambiguous_hosts(host: str) -> None:
    with pytest.raises(AuthorityTokenError):
        normalize_host(host)


def test_host_policy_intersection_keeps_the_narrower_domain() -> None:
    assert intersect_host_policies(
        ["example.com", "uploads.example.net"],
        ["api.example.com", "example.net"],
    ) == frozenset({"api.example.com", "uploads.example.net"})
    assert not intersect_host_policies(["example.com"], ["unrelated.test"])


def test_authority_public_key_validation_rejects_invalid_pem() -> None:
    with pytest.raises(AuthorityTokenError, match="valid PEM"):
        validate_authority_public_key("not a pem")
