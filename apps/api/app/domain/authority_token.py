"""Short-lived, task-scoped authority tokens for isolated runtime services."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)
from jwt.types import Options

from app.domain.capability import Capability, parse_capabilities, sorted_capabilities

AUTHORITY_TOKEN_SCHEMA = "loop.authority-token/v1"
PROVIDER_GATEWAY_AUDIENCE = "loop-provider-gateway"
EGRESS_PROXY_AUDIENCE = "loop-egress-proxy"
AUTHORITY_CONTROL_AUDIENCE = "loop-authority-control"
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class AuthorityTokenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuthorityGrant:
    token_id: str
    task_id: str
    owner_id: str
    project_id: str
    run_id: str
    capabilities: frozenset[Capability]
    egress_hosts: frozenset[str]
    expires_at: datetime

    def permits(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def permits_host(self, host: str) -> bool:
        normalized = normalize_host(host)
        return any(
            normalized == allowed or normalized.endswith(f".{allowed}")
            for allowed in self.egress_hosts
        )


def normalize_host(host: str) -> str:
    raw = host.strip().lower().rstrip(".")
    if not raw or "://" in raw or any(ch in raw for ch in "/?#@*"):
        raise AuthorityTokenError(f"Invalid egress host {host!r}")
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        try:
            ascii_host = raw.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise AuthorityTokenError(f"Invalid egress host {host!r}") from exc
        labels = ascii_host.split(".")
        if len(ascii_host) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
            raise AuthorityTokenError(f"Invalid egress host {host!r}") from None
        if len(labels) < 2 or ascii_host == "localhost":
            raise AuthorityTokenError(f"Egress host must be a public DNS name: {host!r}") from None
        return ascii_host
    if not address.is_global:
        raise AuthorityTokenError(f"Egress IP must be globally routable: {host!r}")
    return address.compressed


def normalize_hosts(hosts: Iterable[str]) -> frozenset[str]:
    return frozenset(normalize_host(host) for host in hosts)


def intersect_host_policies(requested: Iterable[str], ceiling: Iterable[str]) -> frozenset[str]:
    requested_hosts = normalize_hosts(requested)
    ceiling_hosts = normalize_hosts(ceiling)
    intersection: set[str] = set()
    for requested_host in requested_hosts:
        for ceiling_host in ceiling_hosts:
            if requested_host == ceiling_host or requested_host.endswith(f".{ceiling_host}"):
                intersection.add(requested_host)
            elif ceiling_host.endswith(f".{requested_host}"):
                intersection.add(ceiling_host)
    return frozenset(intersection)


def public_key_pem(private_key_pem: str) -> str:
    key = _load_private_key(private_key_pem)
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


def authority_key_id(public_key_pem_value: str) -> str:
    key = _load_public_key(public_key_pem_value)
    public = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"ed25519:{hashlib.sha256(public).hexdigest()[:24]}"


def authority_public_keyring(
    keys: str | Mapping[str, str],
) -> dict[str, Ed25519PublicKey]:
    values = {authority_key_id(keys): keys} if isinstance(keys, str) else dict(keys)
    if not values:
        raise AuthorityTokenError("Authority verification keyring is empty")
    parsed: dict[str, Ed25519PublicKey] = {}
    for configured_id, pem in values.items():
        key = _load_public_key(pem)
        actual_id = authority_key_id(pem)
        if configured_id != actual_id:
            raise AuthorityTokenError(
                f"Authority key id {configured_id!r} does not match its public key"
            )
        parsed[actual_id] = key
    return parsed


def validate_authority_public_key(public_key_pem_value: str) -> None:
    _load_public_key(public_key_pem_value)


def issue_authority_token(
    private_key_pem: str,
    *,
    audience: str,
    task_id: str,
    owner_id: str,
    project_id: str,
    run_id: str,
    capabilities: Iterable[Capability | str],
    egress_hosts: Iterable[str],
    ttl_seconds: int,
    now: datetime | None = None,
) -> str:
    key = _load_private_key(private_key_pem)
    issued_at = (now or datetime.now(UTC)).astimezone(UTC)
    expires_at = issued_at + timedelta(seconds=max(1, ttl_seconds))
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    claims: dict[str, Any] = {
        "schema": AUTHORITY_TOKEN_SCHEMA,
        "iss": "loop-agent",
        "aud": audience,
        "sub": task_id,
        "jti": str(uuid.uuid4()),
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "owner_id": owner_id,
        "project_id": project_id,
        "run_id": run_id,
        "capabilities": sorted_capabilities(parse_capabilities(capabilities)),
        "egress_hosts": sorted(normalize_hosts(egress_hosts)),
    }
    key_id = f"ed25519:{hashlib.sha256(public).hexdigest()[:24]}"
    encoded = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": key_id, "typ": "JWT"})
    return str(encoded)


def verify_authority_token(
    token: str,
    public_keys: str | Mapping[str, str],
    *,
    audience: str,
    now: datetime | None = None,
) -> AuthorityGrant:
    keyring = authority_public_keyring(public_keys)
    options: Options = {
        "require": [
            "schema",
            "iss",
            "aud",
            "sub",
            "jti",
            "iat",
            "nbf",
            "exp",
            "owner_id",
            "project_id",
            "run_id",
            "capabilities",
            "egress_hosts",
        ]
    }
    if now is not None:
        options["verify_exp"] = False
        options["verify_nbf"] = False
    try:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id not in keyring:
            raise AuthorityTokenError("Authority token references an unknown signing key")
        claims = jwt.decode(
            token,
            keyring[key_id],
            algorithms=["EdDSA"],
            audience=audience,
            issuer="loop-agent",
            options=options,
        )
    except jwt.PyJWTError as exc:
        raise AuthorityTokenError(f"Invalid authority token: {exc}") from exc
    if claims.get("schema") != AUTHORITY_TOKEN_SCHEMA:
        raise AuthorityTokenError("Unsupported authority token schema")
    current = (now or datetime.now(UTC)).timestamp()
    if current < float(claims["nbf"]) or current >= float(claims["exp"]):
        raise AuthorityTokenError("Authority token is not currently valid")
    try:
        capabilities = parse_capabilities(claims["capabilities"])
        hosts = normalize_hosts(claims["egress_hosts"])
        expires_at = datetime.fromtimestamp(float(claims["exp"]), tz=UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorityTokenError("Authority token claims are malformed") from exc
    return AuthorityGrant(
        token_id=str(claims["jti"]),
        task_id=str(claims["sub"]),
        owner_id=str(claims["owner_id"]),
        project_id=str(claims["project_id"]),
        run_id=str(claims["run_id"]),
        capabilities=capabilities,
        egress_hosts=hosts,
        expires_at=expires_at,
    )


def _load_private_key(pem: str) -> Ed25519PrivateKey:
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (TypeError, ValueError) as exc:
        raise AuthorityTokenError("Authority signing key is not a valid PEM key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise AuthorityTokenError("Authority signing key must be Ed25519")
    return key


def _load_public_key(pem: str) -> Ed25519PublicKey:
    try:
        key = load_pem_public_key(pem.encode())
    except (TypeError, ValueError) as exc:
        raise AuthorityTokenError("Authority verification key is not a valid PEM key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise AuthorityTokenError("Authority verification key must be Ed25519")
    return key
