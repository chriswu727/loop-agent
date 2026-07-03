"""Signed, capability-scoped skills.

A skill is a folder under the skills root containing ``skill.json`` (a manifest
declaring instructions for the agent and the capability envelope it runs under)
and ``skill.json.sig`` (a detached ed25519 signature over the manifest bytes).
Loop verifies the signature against a configured trust public key *before* the
skill is loaded, and refuses anything unsigned or tampered. A skill's prose can
ask for anything; the runtime grants only what the manifest declared and the
signature vouched for.

This is the structural answer to OpenClaw's extension model — thousands of
unsigned plain-text skills injected straight into the prompt, which let malicious
skills ship infostealers. Here a skill carries provenance and a declared,
enforced envelope, or it does not load.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

MANIFEST = "skill.json"
SIGNATURE = "skill.json.sig"


@dataclass(slots=True)
class SkillManifest:
    name: str
    description: str
    instructions: str
    allowed_tools: list[str] | None  # None = no extra tool restriction
    allow_egress: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillManifest:
        return cls(
            name=str(data.get("name", "")).strip(),
            description=str(data.get("description", "")),
            instructions=str(data.get("instructions", "")),
            allowed_tools=data.get("allowed_tools"),
            allow_egress=bool(data.get("allow_egress", False)),
        )


@dataclass(slots=True)
class Skill:
    manifest: SkillManifest
    verified: bool
    reason: str  # why it is (or isn't) verified


class SkillStore:
    def __init__(self, root: Path, trust_public_key_pem: str | None) -> None:
        self.root = root
        self._trust_key: Ed25519PublicKey | None = None
        if trust_public_key_pem:
            try:
                key = load_pem_public_key(trust_public_key_pem.encode())
                if isinstance(key, Ed25519PublicKey):
                    self._trust_key = key
            except Exception:
                self._trust_key = None

    def list_skills(self) -> list[Skill]:
        if not self.root.is_dir():
            return []
        skills: list[Skill] = []
        for d in sorted(self.root.iterdir()):
            if (d / MANIFEST).is_file():
                skill = self._load_dir(d)
                if skill is not None:
                    skills.append(skill)
        return skills

    def load(self, name: str) -> Skill | None:
        """Return a skill by name only if it verifies; else None."""
        for skill in self.list_skills():
            if skill.manifest.name == name:
                return skill if skill.verified else None
        return None

    def _load_dir(self, d: Path) -> Skill | None:
        manifest_bytes = (d / MANIFEST).read_bytes()
        try:
            manifest = SkillManifest.from_dict(json.loads(manifest_bytes))
        except json.JSONDecodeError:
            return None
        verified, reason = self._verify(d, manifest_bytes)
        return Skill(manifest=manifest, verified=verified, reason=reason)

    def _verify(self, d: Path, manifest_bytes: bytes) -> tuple[bool, str]:
        if self._trust_key is None:
            return False, "no trust key configured"
        sig_path = d / SIGNATURE
        if not sig_path.is_file():
            return False, "missing signature"
        try:
            self._trust_key.verify(sig_path.read_bytes(), manifest_bytes)
        except InvalidSignature:
            return False, "signature does not match (tampered or wrong key)"
        return True, "signature verified"


# --- Signing helpers (for tooling/tests; the server only verifies) ----------


def generate_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem) for an ed25519 keypair."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, encryption_algorithm=_no_encryption()
    ).decode()
    public_pem = (
        private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    )
    return private_pem, public_pem


def sign_skill(skill_dir: Path, private_pem: str) -> None:
    """Sign a skill directory's manifest, writing skill.json.sig."""
    private = load_pem_private_key(private_pem.encode(), password=None)
    assert isinstance(private, Ed25519PrivateKey)
    signature = private.sign((skill_dir / MANIFEST).read_bytes())
    (skill_dir / SIGNATURE).write_bytes(signature)


def _no_encryption() -> NoEncryption:
    return NoEncryption()
