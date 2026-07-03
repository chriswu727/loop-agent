"""Secret redaction: masks known secret shapes, leaves ordinary text alone."""

from __future__ import annotations

from app.core.redaction import redact_secrets


def test_masks_known_secret_shapes() -> None:
    key = "sk-abcdefghij1234567890KLMN"
    assert key not in redact_secrets(f"DEEPSEEK_API_KEY={key}")
    assert "DEEPSEEK_API_KEY" in redact_secrets(f"DEEPSEEK_API_KEY={key}")  # name kept
    ghp = "ghp_abcdefghij1234567890ABCD"
    assert ghp not in redact_secrets(f"token {ghp} leaked")
    assert "hunter2000secret" not in redact_secrets("db_password: hunter2000secret")
    bearer = "abcdefghijklmnop1234567"
    assert bearer not in redact_secrets(f"Authorization: Bearer {bearer}")


def test_masks_pem_private_key_block() -> None:
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "AAAAsecretkeymaterial\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    out = redact_secrets(pem)
    assert "secretkeymaterial" not in out
    assert "REDACTED" in out


def test_leaves_ordinary_text_alone() -> None:
    text = "The file config.txt has 42 lines; the build passed at commit a1b2c3d."
    assert redact_secrets(text) == text
