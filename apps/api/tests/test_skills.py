"""Signed skills must verify, and anything unsigned/tampered/wrong-key must not."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.skills import SkillStore, generate_keypair, sign_skill


def _make_skill(root: Path, name: str, manifest: dict) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "skill.json").write_text(json.dumps(manifest))
    return d


def test_signed_skill_verifies(tmp_path: Path) -> None:
    priv, pub = generate_keypair()
    root = tmp_path / "skills"
    d = _make_skill(
        root,
        "writer",
        {
            "name": "writer",
            "description": "x",
            "instructions": "Write neatly.",
            "allowed_tools": ["write_file", "read_file"],
            "allow_egress": False,
        },
    )
    sign_skill(d, priv)

    store = SkillStore(root, pub)
    s = store.load("writer")
    assert s is not None and s.verified
    assert s.manifest.allowed_tools == ["write_file", "read_file"]
    assert s.manifest.instructions == "Write neatly."


def test_tampered_skill_is_rejected(tmp_path: Path) -> None:
    priv, pub = generate_keypair()
    root = tmp_path / "skills"
    d = _make_skill(root, "w", {"name": "w", "instructions": "ok", "allow_egress": False})
    sign_skill(d, priv)
    # Edit the manifest AFTER signing — escalate egress.
    (d / "skill.json").write_text(
        json.dumps({"name": "w", "instructions": "EVIL", "allow_egress": True})
    )

    store = SkillStore(root, pub)
    assert store.load("w") is None  # not loadable
    listed = store.list_skills()
    assert listed and listed[0].verified is False


def test_unsigned_skill_not_verified(tmp_path: Path) -> None:
    _, pub = generate_keypair()
    root = tmp_path / "skills"
    _make_skill(root, "w", {"name": "w", "instructions": "a"})
    assert SkillStore(root, pub).load("w") is None


def test_wrong_trust_key_rejected(tmp_path: Path) -> None:
    priv, _ = generate_keypair()
    _, other_pub = generate_keypair()
    root = tmp_path / "skills"
    d = _make_skill(root, "w", {"name": "w", "instructions": "a"})
    sign_skill(d, priv)
    assert SkillStore(root, other_pub).load("w") is None


def test_no_trust_key_means_nothing_verifies(tmp_path: Path) -> None:
    priv, _ = generate_keypair()
    root = tmp_path / "skills"
    d = _make_skill(root, "w", {"name": "w", "instructions": "a"})
    sign_skill(d, priv)
    assert SkillStore(root, None).load("w") is None


def test_bundled_example_skill_verifies_with_default_trust_key() -> None:
    """The committed skills/hello-report signs against the committed trust key, so
    the signed-skills feature is live out of the box (not an empty dropdown)."""
    from pathlib import Path

    from app.core.config import settings
    from app.services.skills import SkillStore

    pem = settings.trust_public_key_pem()
    assert pem is not None  # the default file-based trust key loads
    store = SkillStore(Path(settings.agent_skills_root), pem)
    skill = store.load("hello-report")
    assert skill is not None and skill.verified
    assert skill.manifest.allowed_tools == ["write_file", "read_file"]
    assert skill.manifest.allow_egress is False
