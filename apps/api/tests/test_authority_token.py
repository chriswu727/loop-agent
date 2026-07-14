from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.domain.authority_token import (
    EGRESS_PROXY_AUDIENCE,
    PROVIDER_GATEWAY_AUDIENCE,
    AuthorityTokenError,
    authority_key_id,
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


def test_authority_keyring_accepts_active_and_retiring_signing_keys() -> None:
    first_private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    second_private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    public_keys = [public_key_pem(first_private), public_key_pem(second_private)]
    keyring = {authority_key_id(key): key for key in public_keys}

    for private in (first_private, second_private):
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
        assert (
            verify_authority_token(token, keyring, audience=PROVIDER_GATEWAY_AUDIENCE).run_id
            == "task-1:1"
        )


def test_authority_keyring_rejects_unknown_or_mislabeled_keys(
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
        capabilities=[],
        egress_hosts=[],
        ttl_seconds=120,
    )
    unrelated_private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    unrelated_public = public_key_pem(unrelated_private)
    with pytest.raises(AuthorityTokenError, match="unknown signing key"):
        verify_authority_token(
            token,
            {authority_key_id(unrelated_public): unrelated_public},
            audience=PROVIDER_GATEWAY_AUDIENCE,
        )
    with pytest.raises(AuthorityTokenError, match="does not match"):
        verify_authority_token(
            token,
            {"ed25519:wrong": public},
            audience=PROVIDER_GATEWAY_AUDIENCE,
        )


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
