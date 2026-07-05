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
from itertools import pairwise
from urllib.parse import urlsplit


class Verdict(enum.StrEnum):
    ALLOW = "allow"
    NEEDS_APPROVAL = "needs_approval"
    DENY = "deny"


# Patterns that must never run, whatever the mode. Case-insensitive.
_DENY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), reason)
    for p, reason in [
        # rm with BOTH a recursive and a force flag, in any order/spelling: -rf, -fr,
        # -r -f, --recursive --force, -r --force, ... Two lookaheads scoped to this
        # command's args (stop at ; | & newline) so we don't match across commands.
        (
            r"\brm\b(?=[^;|&\n]*(?:-[a-z]*r|--recursive))(?=[^;|&\n]*(?:-[a-z]*f|--force))",
            "recursive force-delete",
        ),
        # rm -r / (recursive delete of a broad path, even without a force flag)
        (
            r"\brm\b(?=[^;|&\n]*(?:-[a-z]*r|--recursive))[^;|&\n]*\s(/|~|\*)",
            "recursive delete of a broad path",
        ),
        # The Python equivalents of rm -rf. Since `python` is allowlisted and the
        # -c inner is scanned, a library call (no shell danger-word) would otherwise
        # slip straight through the shell-oriented rules above and, inline, delete
        # host files. Blocked broadly, like rm -rf is.
        (r"\bshutil\.rmtree\b|\bos\.removedirs\b", "recursive delete via a Python library"),
        # Same hole for the other allowlisted interpreter: node's fs.rm/rmdir with
        # {recursive:true}. Match the recursive option so single-file deletes aren't
        # over-blocked.
        (r"\b(rm|rmdir)(Sync)?\s*\([^;\n]*recursive", "recursive delete via a Node fs call"),
        (r"\b(sudo|doas|pkexec)\b|\bsu\s+-", "privilege escalation"),
        # mkfs at a command position (start / after a separator / after sudo), so
        # `cat mkfs.md` (a filename) isn't flagged.
        (
            r"(?:^|[\n;|&(]\s*|\b(?:sudo|doas|nohup|env|time|exec)\s+)mkfs(\.\w+)?\b",
            "format a filesystem",
        ),
        # WRITING to a raw block device (of=, redirect, tee, cp) — destroys the
        # disk. Reads (dd if=/dev/sda of=backup.img) and /dev/null|stdout are fine,
        # and a plain file-to-file dd is no longer over-blocked.
        (
            r"(\bof=/dev/|>\s*/dev/|\btee\b[^;|&\n]*/dev/|\bcp\b[^;|&\n]*\s/dev/)"
            r"(sd|nvme|disk|hd|vd|mmcblk|loop)",
            "writing to a raw block device",
        ),
        (r"\b(shutdown|reboot|halt|poweroff)\b|\b(init|telinit)\s+[06]\b", "power control"),
        # A function whose body pipes to itself and backgrounds — `:(){ :|:& };:`
        # and named variants like `bomb(){ bomb|bomb & };bomb`.
        # [^}&|] / [^}&] (not [^}]) so the two runs can't re-consume the | and & —
        # avoids O(n^2) backtracking on a crafted `x(){ a|a|a|...`.
        (r"(?:\b\w+|:)\s*\(\s*\)\s*\{[^}&|]*\|[^}&]*&", "fork bomb"),
        # Piping the network into ANY interpreter, through any intermediate pipe
        # stages (`curl x | tee f | bash`), not just sh/bash/zsh.
        (
            r"\b(curl|wget|fetch|aria2c|axel)\b[^;&\n]*\|\s*(sudo\s+)?"
            r"(sh|bash|zsh|dash|python3?|perl|ruby|node|php)\b",
            "piping the network into an interpreter",
        ),
        # chmod 777 (or 0777) on a broad path, in any flag order/spelling.
        (
            r"\bchmod\b(?=[^;|&\n]*\b0?777\b)(?=[^;|&\n]*\s(?:/|~))",
            "world-writable on a broad path",
        ),
        (r"/etc/(passwd|shadow|gshadow|sudoers)", "touching system credential files"),
        # Reverse shells: netcat -e, socat EXEC/SYSTEM, or a shell wired to /dev/tcp.
        (
            r"\bn(c|cat)\b[^;|&\n]*\s-[a-z]*[ec]\b|\bsocat\b[^;|&\n]*(exec|system):|>&\s*/dev/tcp",
            "reverse shell",
        ),
        # The interpreter equivalents: redirecting a socket fd onto stdio
        # (os.dup2(s.fileno(), 0)) or grabbing a TTY (pty.spawn) — the classic python
        # reverse-shell signatures, ~never legitimate. Keeps the reverse-shell block
        # consistent across shell and allowlisted interpreters.
        (r"\bdup2\b[^;\n]*\bfileno\b|\bpty\.spawn\b", "interpreter reverse shell"),
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
        (r"\b(nc|ncat|telnet|socat)\b", "raw socket"),
        (r"\b(ssh|scp|sftp|rsync)\b", "remote shell/copy"),
        (r"\bftp\b", "ftp"),
        (r"\bgit\s+(clone|pull|push|fetch|ls-remote)\b", "git network op"),
        (r"\bpip3?\s+(install|download)\b", "pip download"),
        (r"\buv\s+(pip\s+)?(install|add|sync)\b", "uv install"),
        (r"\b(npm|pnpm|yarn)\s+(install|add|ci|i)\b", "node package install"),
        (r"\bbrew\s+(install|update|upgrade)\b", "brew"),
        (r"\bapt(-get)?\s+(install|update)\b", "apt"),
        (r"\bgo\s+get\b|\bcargo\s+(install|add|fetch)\b", "package fetch"),
        # Bash pseudo-device sockets — the classic way to open a connection with
        # no network binary at all (`echo x > /dev/tcp/host/port`).
        (r"/dev/(tcp|udp)/", "bash /dev/tcp socket"),
        (r"\b(aria2c|axel|wget2|httpie)\b", "downloader"),
        (r"\b(lynx|w3m|links|elinks)\b", "text browser"),
        (r"\b(dig|nslookup|ping|traceroute|tracepath)\b", "network probe"),
        # Inline interpreter code that reaches the network — via `-c`, a heredoc
        # (`python3 <<'EOF' ... urllib ... EOF`), or stdin; -c is NOT required. The
        # gap is `[^;&|]*` so it spans a heredoc's newlines but NOT a shell separator
        # (else `python app.py && grep "requests.get(" src` would over-block an
        # offline grep). Precise tokens (actual imports/calls, ruby/perl idioms, or a
        # QUOTED url) so `socket.csv` isn't a false positive. Container mode is the
        # real enforcement (--network none).
        (
            r"\b(python3?|node|deno|bun|ruby|perl|php)\b[^;&|]*"
            r"(import\s+(urllib|requests|httpx|socket|http|aiohttp|ftplib|smtplib)|"
            r"urllib\.|requests\.(get|post|put|delete|patch|head|request|Session)|"
            r"urlopen|socket\.socket|http\.client|aiohttp\.|net/http|Net::HTTP|\bLWP\b|IO::Socket|"
            r"fetch\s*\(|require\(\s*['\"](?:node:)?(?:https?|net|dgram|tls|dns|http2)\b|"
            r"\bnet\.(?:connect|createConnection)|\bhttps?\.(?:request|get)\b|"
            r"open-uri|file_get_contents|['\"]https?://)",
            "interpreter network access",
        ),
    ]
)


# Network access inside a *file* the agent runs (e.g. `python fetch.py` where
# fetch.py imports urllib). The command string alone looks innocent, so with
# default-deny egress on the inline path we also scan the referenced script.
_NET_IN_CODE = re.compile(
    r"(urllib|requests|httpx|http\.client|aiohttp|urlopen|socket\.|ftplib|smtplib|"
    r"net/http|open-uri|file_get_contents|fetch\s*\(|https?://|/dev/tcp/|"
    # node's low-level network modules — net.connect / http.request / require('net')
    # etc. carry no URL or socket. token, so without these they slip past (a raw-socket
    # egress bypass in inline mode; the container's --network none always blocks it).
    r"require\(\s*['\"](?:net|http|https|http2|dgram|dns|tls)['\"]\s*\)|"
    r"\bnet\.(?:connect|createConnection|Socket)|\bhttps?\.(?:request|get)\b)",
    re.IGNORECASE,
)
_SCRIPT_ARG = re.compile(r"(?:^|/)[\w.\-]+\.(py|js|mjs|cjs|ts|rb|pl|php|sh|bash)$", re.IGNORECASE)
_INTERPRETERS = frozenset(
    {"python", "python3", "node", "deno", "bun", "ruby", "perl", "php", "sh", "bash"}
)


def code_network_reason(code: str) -> str | None:
    """If a script's contents reach the network, why; else None."""
    return "network code in script" if _NET_IN_CODE.search(code) else None


def script_paths_in(command: str) -> list[str]:
    """Script arguments a command runs, to scan their contents for network code:
    files with a known extension, plus — when the program is an interpreter — its
    first non-flag argument even without an extension (`python3 grab`)."""
    import shlex

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    paths = [t for t in tokens if _SCRIPT_ARG.search(t)]
    if tokens and tokens[0].rsplit("/", 1)[-1] in _INTERPRETERS:
        for t in tokens[1:]:
            if t.startswith("-"):
                continue
            if t not in paths:
                paths.append(t)  # first real arg, scanned regardless of extension
            break
    return paths


def network_command_reason(command: str) -> str | None:
    """If the command reaches the network, why; else None."""
    for pattern, reason in _NETWORK:
        if pattern.search(command):
            return reason
    return None


# Destination hosts a command targets — for a per-host egress allowlist. Parse the
# authority with urlsplit so userinfo (`user@host`) and port are dropped, closing the
# `http://allowed@evil.com` decoy bypass. Cover scheme-less network-binary targets too.
_URL = re.compile(r"https?://[^\s'\"|;&<>`)]+", re.IGNORECASE)
_HOST_FIRST = ("nc", "ncat", "telnet", "ssh")  # first positional is [user@]host
_HOST_COLON = ("scp", "sftp", "rsync")  # the remote is a [user@]host:path token
_FETCHER = (
    "curl",
    "wget",
    "wget2",
    "aria2c",
    "axel",
    "httpie",
    "lftp",
    "ftp",
    "ftps",
    "lynx",
    "w3m",
    "links",
    "elinks",
)  # host is a URL or a bare domain/IP arg
_BIN_RE = re.compile(
    r"\b(" + "|".join(_HOST_FIRST + _HOST_COLON + _FETCHER) + r")\b", re.IGNORECASE
)
# A token that looks like a hostname/IP (so a fetcher's `POST`/`output.txt` args aren't hosts).
_HOSTISH = re.compile(
    r"^(?:\[[0-9a-fA-F:]+\]|(?:[A-Za-z0-9\-]+\.)+[A-Za-z0-9\-]{2,}|\d{1,3}(?:\.\d{1,3}){3})"
)
# Flags whose VALUE is a destination host/URL even without a scheme (`curl --url evil.com`,
# `-x proxy`) — so we extract those instead of skipping them as ordinary flag values.
_HOST_FLAGS = {"--url", "--proxy", "-x", "--connect-to", "--resolve"}


def _host_of(authority: str) -> str | None:
    """Host from an authority (``user@host:port``) or bare host — userinfo and port
    dropped via urlsplit, so a decoy before ``@`` can't mask the real host."""
    a = authority.strip().strip("/")
    if not a:
        return None
    if "://" in a:
        a = a.split("://", 1)[1]
    try:
        h = urlsplit("//" + a).hostname
    except ValueError:
        return None
    return h.lower() if h else None


def destination_hosts(text: str) -> set[str]:
    """Best-effort set of destination hostnames a command (or script) reaches, for the
    egress allowlist. Handles URLs (userinfo/port dropped), scheme-less network-binary
    targets, and scp/ssh ``user@host``/``host:path``. Errs toward extracting a host."""
    # Drop full-line and trailing shell comments so a URL only in a comment isn't a
    # target (best-effort — a `#` not preceded by whitespace, e.g. a URL fragment, stays).
    stripped = "\n".join(re.sub(r"(^|\s)#.*$", "", ln) for ln in text.splitlines())

    hosts: set[str] = set()
    for m in _URL.finditer(stripped):
        h = _host_of(m.group(0).split("://", 1)[1])
        if h:
            hosts.add(h)

    for bm in _BIN_RE.finditer(stripped):
        binary = bm.group(1).lower()
        segment = re.split(r"[;\n|&]", stripped[bm.end() :], maxsplit=1)[0]
        toks = segment.split()
        # Host-valued flags: the value IS a host (`--url evil.com`, `-x proxy`), so
        # extract it — separate from the positional pass, which skips flag values.
        for prev, tok in pairwise(toks):
            if prev.split("=", 1)[0] in _HOST_FLAGS:
                h = _host_of(tok.split(":", 1)[0] if ":" in tok and "//" not in tok else tok)
                if h:
                    hosts.add(h)
        prev_flag = False
        for tok in toks:
            if tok.startswith("-"):
                prev_flag = True
                continue
            skip = prev_flag  # a value belonging to the preceding flag, not a host
            prev_flag = False
            if skip:
                continue
            if "://" in tok:
                h = _host_of(tok.split("://", 1)[1])
                if h:
                    hosts.add(h)
                break
            if binary in _HOST_COLON:
                if ":" in tok:  # [user@]host:path — the remote; a bare source file isn't
                    h = _host_of(tok.split(":", 1)[0])
                    if h:
                        hosts.add(h)
                        break
                continue
            authority = tok.split("/", 1)[0]
            if binary in _FETCHER and not _HOSTISH.match(authority):
                continue  # a fetcher arg that isn't host-shaped (e.g. POST)
            h = _host_of(authority)
            if h:
                hosts.add(h)
            break
    return hosts


def _deny_scan_text(command: str) -> str:
    """The text the deny patterns match against: quoted string literals blanked so
    a keyword inside a git message or grep argument isn't read as a command — but
    the inner code of a shell/interpreter ``-c``/``-e`` argument re-appended
    unquoted, so ``bash -c "rm -rf /"`` is still caught."""
    inners = [m.group(2) for m in re.finditer(r"-[a-z]*[ce]\s+(['\"])(.*?)\1", command, re.DOTALL)]
    blanked = re.sub(r"(['\"]).*?\1", " ", command, flags=re.DOTALL)
    # Join the inners with `; ` (and prepend one) so each starts at a command
    # position — the command-anchored patterns (mkfs, power) match `bash -c "mkfs …"`.
    return blanked + ("; " + "; ".join(inners) if inners else "")


def evaluate_command(command: str) -> tuple[Verdict, str]:
    cmd = command.strip()
    if not cmd:
        return Verdict.DENY, "empty command"
    scan = _deny_scan_text(cmd)
    for pattern, reason in _DENY:
        if pattern.search(scan):
            return Verdict.DENY, reason
    first = re.split(r"\s+", cmd, maxsplit=1)[0]
    first = first.rsplit("/", 1)[-1]  # normalise /usr/bin/python -> python
    if first in _ALLOW_PREFIXES:
        return Verdict.ALLOW, "allowlisted command"
    return Verdict.NEEDS_APPROVAL, f"{first!r} is not on the allowlist"
