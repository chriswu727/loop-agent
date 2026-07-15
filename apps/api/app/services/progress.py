"""Bounded history and deterministic no-progress detection for agent runs."""

from __future__ import annotations

import hashlib
import re
import shlex
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.tools import ToolStatus

_MUTATION_TOOLS = {"write_file", "edit_file"}


@dataclass(slots=True)
class HistoryEntry:
    number: int
    thought: str
    tool: str
    args: dict[str, Any]
    observation: str
    status: ToolStatus

    def render(self) -> str:
        arg_preview = ", ".join(f"{key}={str(value)[:60]!r}" for key, value in self.args.items())
        observation_limit = 1_600 if self.tool.startswith(("sibyl_", "argus_", "browser_")) else 600
        obs = (
            self.observation
            if len(self.observation) <= observation_limit
            else self.observation[:observation_limit] + " …[truncated]"
        )
        return (
            f"Step {self.number} [{self.tool}] ({self.status.value}): {self.thought}\n"
            f"  args: {arg_preview}\n  -> [DATA] {obs}"
        )


def _normalise(value: object, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()[:limit]


def _compact(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _command_signature(command: str, revision: int) -> tuple[str, bool]:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return f"command:{revision}:(empty)", False
    executable = Path(parts[0]).name.lower()
    values = [part for part in parts[1:] if not part.startswith("-")]
    if executable in {"cat", "head", "tail", "wc", "sed"} and values:
        return f"inspect:{revision}:{_normalise(values[-1])}", True
    if executable in {"grep", "rg"}:
        pattern = _normalise(values[0] if values else "")
        root = _normalise(values[-1] if len(values) > 1 else ".")
        return f"search:{revision}:{pattern}:{root}", True
    if executable in {"find", "ls"}:
        return f"explore:{revision}:{executable}:{_normalise(' '.join(values))}", True
    return f"command:{revision}:{_normalise(command, 240)}", False


def _action_signature(tool: str, args: dict[str, Any], revision: int) -> tuple[str, bool]:
    if tool in _MUTATION_TOOLS:
        path = _normalise(args.get("path", ""))
        payload = str(args.get("content", args.get("new", ""))).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()[:12]
        return f"mutate:{path}:{digest}", False
    if tool == "read_file":
        return f"inspect:{revision}:{_normalise(args.get('path', ''))}", True
    if tool == "run_command":
        return _command_signature(str(args.get("command", "")), revision)
    if tool.startswith("browser_"):
        target = next(
            (args[key] for key in ("url", "query", "ref", "selector", "text") if args.get(key)),
            args,
        )
        branches = ("navigate", "search", "snapshot")
        return (
            f"browser:{revision}:{tool}:{_normalise(target, 240)}",
            any(branch in tool for branch in branches),
        )
    if tool.startswith("sibyl_"):
        target = args.get("query") or args.get("url") or args
        return f"research:{revision}:{tool}:{_normalise(target, 240)}", True
    if tool in {"read_inbox", "list_events", "see_image"}:
        return f"inspect:{revision}:{tool}:{_normalise(args, 240)}", True
    return f"{tool}:{revision}:{_normalise(args, 240)}", False


def compact_history(entries: list[HistoryEntry]) -> str:
    touched: list[str] = []
    outcomes: list[str] = []
    failures: list[str] = []
    attempts: Counter[str] = Counter()
    revision = 0
    for entry in entries:
        signature, exploratory = _action_signature(entry.tool, entry.args, revision)
        attempts[signature.split(":", 2)[0]] += 1
        target = entry.args.get("path") or entry.args.get("command") or entry.args.get("url")
        compact_target = _compact(target, 120)
        if entry.tool in _MUTATION_TOOLS and compact_target and compact_target not in touched:
            touched.append(compact_target)
        outcome = _compact(entry.observation, 180) or "no output"
        line = f"step {entry.number} {entry.tool}({_compact(target, 80)}): [DATA] {outcome}"
        if entry.status is ToolStatus.OK:
            if entry.tool in {"ask_user", "finish", "run_command", "spawn"} or exploratory:
                outcomes.append(line)
        else:
            failures.append(line)
        if entry.tool in _MUTATION_TOOLS and entry.status is ToolStatus.OK:
            revision += 1

    lines = ["[COMPACTED EARLIER STATE — do not retry failed branches without new evidence]"]
    if touched:
        lines.append("[DATA] Artifacts touched: " + ", ".join(touched[:12]))
    if outcomes:
        lines.append("Evidence/results:\n" + "\n".join(f"- {line}" for line in outcomes[-8:]))
    if failures:
        lines.append(
            "Failed/blocked branches:\n" + "\n".join(f"- {line}" for line in failures[-8:])
        )
    lines.append(
        "[DATA] Action mix: " + ", ".join(f"{name}={count}" for name, count in attempts.items())
    )
    return "\n".join(lines)


class ProgressGuard:
    def __init__(self, history: list[HistoryEntry]) -> None:
        self.revision = 0
        self.action_counts: Counter[str] = Counter()
        self.evidence: set[str] = set()
        self.exploration: set[str] = set()
        self.no_progress = 0
        for entry in history:
            self.observe(entry.tool, entry.args, entry.observation, entry.status)

    def preflight(self, tool: str, args: dict[str, Any]) -> str | None:
        signature, exploratory = _action_signature(tool, args, self.revision)
        repeat_limit = 1 if tool.startswith("sibyl_") else settings.agent_repeated_action_limit
        repeated = self.action_counts[signature] >= repeat_limit
        if tool not in _MUTATION_TOOLS and repeated:
            return (
                f"Blocked: semantically equivalent action '{signature}' already ran "
                f"{self.action_counts[signature]} times in the current workspace state. "
                "Use the evidence already collected, change the workspace, or take a "
                "different branch."
            )
        if (
            exploratory
            and signature not in self.exploration
            and len(self.exploration) >= settings.agent_exploration_branch_cap
        ):
            return (
                f"Blocked: exploration branch cap ({settings.agent_exploration_branch_cap}) "
                "reached without changing the workspace. Implement from the evidence, run a "
                "verification, ask the user if genuinely blocked, or finish."
            )
        return None

    def observe(
        self,
        tool: str,
        args: dict[str, Any],
        observation: str,
        status: ToolStatus,
        *,
        force_no_progress: bool = False,
        workspace_changed: bool = False,
    ) -> bool:
        signature, exploratory = _action_signature(tool, args, self.revision)
        seen_action = self.action_counts[signature] > 0
        self.action_counts[signature] += 1
        if exploratory:
            self.exploration.add(signature)

        if status is ToolStatus.OK and (tool in _MUTATION_TOOLS or workspace_changed):
            progress = (not seen_action or workspace_changed) and not force_no_progress
            self.revision += 1
            self.exploration.clear()
        elif status is ToolStatus.OK:
            evidence = hashlib.sha256(_normalise(observation, 2000).encode("utf-8")).hexdigest()
            evidence_key = f"{self.revision}:{evidence}"
            progress = evidence_key not in self.evidence and not force_no_progress
            self.evidence.add(evidence_key)
        else:
            progress = False

        self.no_progress = 0 if progress else self.no_progress + 1
        return progress

    def state(self, reserve: int) -> str:
        return (
            f"workspace revision={self.revision}; exploration branches="
            f"{len(self.exploration)}/{settings.agent_exploration_branch_cap}; "
            f"consecutive no-progress actions={self.no_progress}/"
            f"{settings.agent_stuck_threshold}; verification reserve={reserve} tokens"
        )
