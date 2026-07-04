"""Mask secrets in tool output before it reaches the model, the ledger, or the API.

Loop scrubs its own environment, but a command or file the agent reads can still
surface a credential (``cat`` a config, a stray token in a log). A "safe" agent
should not forward those to the LLM provider or seal them into the step ledger, so
observations pass through here first. Conservative by design: it targets literals
with known secret shapes (provider key prefixes, PEM private keys, bearer tokens)
and values of assignments whose *name* looks secret — not arbitrary long strings.
"""

from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

# Whole PEM private-key blocks.
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Literals with a recognisable secret shape (specific prefixes, so no false hits
# on ordinary hashes/ids).
_TOKENS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),  # Anthropic
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),  # OpenAI / DeepSeek style
    re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),  # Google API key
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),  # bearer tokens
]

# KEY = value / KEY: value where the key name reads like a secret — mask the value,
# keep the name so the agent still knows the setting exists. The secret word is
# bounded by non-letters so `author:`/`tokenizer:` don't match (but underscore
# compounds like GITHUB_TOKEN / DB_PASSWORD do); the key runs are LENGTH-BOUNDED to
# avoid O(n^2) backtracking; and the value may be bare OR quoted (the common
# .env / JSON / YAML form) — the inner value is masked, the quotes kept.
_ASSIGN = re.compile(
    r"""(?ix)
    ( ['"]? [\w.\-]{0,60}
      (?<![A-Za-z])
      (?: secret | token | password | passwd | api[_-]?key
        | access[_-]?key | private[_-]?key | auth )
      (?![A-Za-z])
      [\w.\-]{0,60} ['"]? )          # group 1: the key (with any surrounding quotes)
    ( \s* [=:] \s* )                 # group 2: the = or : delimiter
    (?: (['"]) [^'"]{4,} ['"]        # groups 3: quoted value
      | [^\s'"]{6,} )                # or a bare value
    """
)
_MAX_REDACT = 200_000  # skip the assignment pass on very large blobs (belt-and-suspenders)


def _mask_assignment(m: re.Match[str]) -> str:
    quote = m.group(3)
    value = f"{quote}{_REDACTED}{quote}" if quote else _REDACTED
    return f"{m.group(1)}{m.group(2)}{value}"


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = _PEM.sub("[REDACTED PRIVATE KEY]", text)
    for pattern in _TOKENS:
        out = pattern.sub(_REDACTED, out)
    if len(out) <= _MAX_REDACT:
        out = _ASSIGN.sub(_mask_assignment, out)
    return out
