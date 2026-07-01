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
        "python",
        "python3",
        "pip",
        "pip3",
        "uv",
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "ls",
        "cat",
        "echo",
        "pwd",
        "mkdir",
        "touch",
        "head",
        "tail",
        "wc",
        "tree",
        "grep",
        "rg",
        "find",
        "sed",
        "awk",
        "sort",
        "uniq",
        "cut",
        "diff",
        "cmp",
        "cp",
        "mv",
        "true",
        "false",
        "env",
        "printf",
        "which",
        "type",
        "date",
        "test",
        "pytest",
        "ruff",
        "mypy",
        "black",
        "tsc",
        "eslint",
        "prettier",
        "go",
        "cargo",
        "rustc",
        "javac",
        "java",
        "make",
        "cmake",
        "git",
        "jq",
        "sqlite3",
    }
)


# Commands that reach the network. Used to enforce default-deny egress: unless a
# task declares it may reach the network, these are blocked. Pattern-based v1 —
# real enforcement (network namespace) arrives with container execution.
_NETWORK: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), reason)
    for p, reason in [
        (r"\bcurl\b", "curl"),
        (r"\bwget\b", "wget"),
        (r"\b(nc|ncat|telnet)\b", "raw socket"),
        (r"\b(ssh|scp|sftp|rsync)\b", "remote shell/copy"),
        (r"\bftp\b", "ftp"),
        (r"\bgit\s+(clone|pull|push|fetch|ls-remote)\b", "git network op"),
        (r"\bpip3?\s+(install|download)\b", "pip download"),
        (r"\buv\s+(pip\s+)?(install|add|sync)\b", "uv install"),
        (r"\b(npm|pnpm|yarn)\s+(install|add|ci|i)\b", "node package install"),
        (r"\bbrew\s+(install|update|upgrade)\b", "brew"),
        (r"\bapt(-get)?\s+(install|update)\b", "apt"),
        (r"\bgo\s+get\b|\bcargo\s+(install|add|fetch)\b", "package fetch"),
        # Interpreter one-liners that reach the network — the obvious way to slip
        # past the token denylist (`python3 -c "import urllib; urlopen(...)"`).
        # Best-effort on the inline path; container mode enforces --network none.
        (
            r"\b(python3?|node|deno|bun|ruby|perl|php)\b.*-[ce]\b.*"
            r"(urllib|requests|httpx|http\.client|socket|urlopen|ftplib|smtplib|"
            r"net/http|open-uri|file_get_contents|fetch\s*\(|https?://)",
            "interpreter network access",
        ),
    ]
)


def network_command_reason(command: str) -> str | None:
    """If the command reaches the network, why; else None."""
    for pattern, reason in _NETWORK:
        if pattern.search(command):
            return reason
    return None


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
