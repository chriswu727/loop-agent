"""Command safety policy.

The agent can run shell commands, which is powerful and therefore the riskiest
surface in the product. This module classifies a command into one of three
verdicts so the loop can decide what to do with it:

  * ALLOW          — a known-safe command (allowlisted first token).
  * NEEDS_APPROVAL — not obviously dangerous, but not on the allowlist either.
                     In ``auto`` mode it runs; in ``manual`` mode it waits for
                     the user.
  * DENY           — matches a destructive/exfiltration pattern; never runs.

This is a denylist-plus-allowlist, not a true jail: it stops the obvious
foot-guns (wiping the disk, fork bombs, piping the internet into a shell) and
keeps work confined by running everything from the workspace directory. Real
isolation (containers) is a later milestone, documented in docs/loop.md.
"""

from __future__ import annotations

import enum
import re


class Verdict(enum.StrEnum):
    ALLOW = "allow"
    NEEDS_APPROVAL = "needs_approval"
    DENY = "deny"


# Patterns that must never run, whatever the mode. Case-insensitive.
_DENY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), reason)
    for p, reason in [
        (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "recursive force-delete"),
        (r"\brm\s+-[a-z]*r[a-z]*\s+(/|~|\*)", "recursive delete of a broad path"),
        (r"\bsudo\b|\bsu\s+-", "privilege escalation"),
        (r"\bmkfs\b|\bdd\s+if=", "raw disk write"),
        (r">\s*/dev/(sd|nvme|disk)", "writing to a raw device"),
        (r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b", "power control"),
        (r":\s*\(\s*\)\s*\{", "fork bomb"),
        (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b", "piping the network into a shell"),
        (r"\bchmod\s+-R\s+777\s+/", "world-writable on a broad path"),
        (r"/etc/(passwd|shadow|sudoers)", "touching system credential files"),
        (r"\bnc\b\s+-[a-z]*e|\bncat\b.*-e", "netcat reverse shell"),
    ]
)

# First tokens we consider safe to run without asking.
_ALLOW_PREFIXES = frozenset(
    {
        "python", "python3", "pip", "pip3", "uv", "node", "npm", "npx", "pnpm", "yarn",
        "ls", "cat", "echo", "pwd", "mkdir", "touch", "head", "tail", "wc", "tree",
        "grep", "rg", "find", "sed", "awk", "sort", "uniq", "cut", "diff", "cmp",
        "cp", "mv", "true", "false", "env", "printf", "which", "type", "date", "test",
        "pytest", "ruff", "mypy", "black", "tsc", "eslint", "prettier",
        "go", "cargo", "rustc", "javac", "java", "make", "cmake", "git", "jq", "sqlite3",
    }
)


def evaluate_command(command: str) -> tuple[Verdict, str]:
    cmd = command.strip()
    if not cmd:
        return Verdict.DENY, "empty command"
    for pattern, reason in _DENY:
        if pattern.search(cmd):
            return Verdict.DENY, reason
    first = re.split(r"\s+", cmd, maxsplit=1)[0]
    first = first.rsplit("/", 1)[-1]  # normalise /usr/bin/python -> python
    if first in _ALLOW_PREFIXES:
        return Verdict.ALLOW, "allowlisted command"
    return Verdict.NEEDS_APPROVAL, f"{first!r} is not on the allowlist"
