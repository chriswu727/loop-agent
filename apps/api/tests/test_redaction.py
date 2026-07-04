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


def test_masks_quoted_and_json_secret_values() -> None:
    # The common .env / JSON / YAML form: quoted values must be masked, quotes kept.
    assert "hunter2000secret" not in redact_secrets("PASSWORD='hunter2000secret'")
    assert "abcdef123456" not in redact_secrets('API_KEY="abcdef123456"')
    assert "abcdef123456" not in redact_secrets('"api_key": "abcdef123456"')
    # Underscore-compound keys still redact (a \b would have broken these).
    assert "REDACTED" in redact_secrets("GITHUB_TOKEN=plainvalue123456")
    assert "REDACTED" in redact_secrets("DB_PASSWORD=supersecretvalue")


def test_does_not_over_redact_benign_key_values() -> None:
    # A secret keyword as a substring of an ordinary word must NOT trigger redaction.
    for benign in ("author: Shakespeare", "tokenizer: sentencepiece", "the authority approved"):
        assert redact_secrets(benign) == benign


def test_assignment_redaction_is_not_redos() -> None:
    # A long class-run with no delimiter used to backtrack ~28s; must be fast now.
    import time

    start = time.time()
    redact_secrets("a" * 60000 + " no delimiter here")
    assert time.time() - start < 1.0
