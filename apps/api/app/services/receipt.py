"""The Receipt: a re-checkable, content-addressed record of what a task did.

Every accepted task writes ``receipt.json`` + ``RECEIPT.md`` into its workspace.
The receipt records the goal, the rubric it was graded against, every machine
check and its verdict, the score, *how* it was verified (re-execution vs
judgment), the run accounting, and a sha256 of every output file. Hashing the
canonical receipt yields a content address: change any recorded fact and the
hash changes. This is what makes Loop's "done" a fact you can audit and replay,
not a claim in a chat log.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db.models.task import TaskModel
from app.services.verification import CheckResult, as_dicts
from app.tools import Workspace

RECEIPT_JSON = "receipt.json"
RECEIPT_MD = "RECEIPT.md"


def _file_manifest(workspace: Workspace) -> list[dict[str, Any]]:
    """sha256 + size of every workspace file, excluding the receipt itself."""
    manifest: list[dict[str, Any]] = []
    for rel, size in workspace.list_files():
        if rel in (RECEIPT_JSON, RECEIPT_MD):
            continue
        digest = hashlib.sha256(workspace.resolve(rel).read_bytes()).hexdigest()
        manifest.append({"path": rel, "size": size, "sha256": digest})
    return manifest


def build_receipt(
    task: TaskModel,
    check_results: list[CheckResult],
    *,
    score: int,
    verified_by: str,
    workspace: Workspace,
    ledger_head: str = "",
) -> tuple[str, dict[str, Any]]:
    """Assemble the receipt, write it into the workspace, return (hash, receipt)."""
    checks = as_dicts(check_results)
    body: dict[str, Any] = {
        "task_id": str(task.id),
        "goal": task.goal,
        "rubric": task.rubric or [],
        "verified_by": verified_by,  # "execution" | "judgment"
        "score": score,
        "checks": checks,
        "checks_passed": all(c["passed"] for c in checks) if checks else None,
        "steps_used": task.steps_used,
        "tokens_used": task.tokens_used,
        # Head of the tamper-evident step chain — this Receipt vouches for the
        # entire history that produced it.
        "ledger_head": ledger_head,
        "files": _file_manifest(workspace),
    }
    # Content address: hash the canonical body (stable key order, no whitespace
    # drift), then store the hash alongside it.
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    receipt_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    receipt = {"receipt_hash": receipt_hash, **body}

    workspace.write(RECEIPT_JSON, json.dumps(receipt, indent=2, ensure_ascii=False))
    workspace.write(RECEIPT_MD, _render_markdown(receipt))
    return receipt_hash, receipt


def _render_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# Task Receipt",
        "",
        f"- **Verified by:** {receipt['verified_by']}"
        + ("" if receipt["verified_by"] == "execution"
           else " — _not re-executed; graded by judgment_"),
        f"- **Score:** {receipt['score']}/100",
        f"- **Steps:** {receipt['steps_used']} · **Tokens:** {receipt['tokens_used']}",
        f"- **Ledger head:** `{receipt['ledger_head']}`",
        f"- **Receipt hash:** `{receipt['receipt_hash']}`",
        "",
        "## Goal",
        receipt["goal"],
        "",
    ]
    if receipt["rubric"]:
        lines += ["## Success criteria", *[f"- {c}" for c in receipt["rubric"]], ""]
    if receipt["checks"]:
        lines.append("## Checks (re-run on a fresh copy of the workspace)")
        for c in receipt["checks"]:
            mark = "PASS" if c["passed"] else "FAIL"
            lines.append(f"- [{mark}] `{c['kind']}` {c['target']} — {c['evidence']}")
        lines.append("")
    if receipt["files"]:
        lines.append("## Output files")
        for f in receipt["files"]:
            lines.append(f"- `{f['path']}` ({f['size']} b) sha256 `{f['sha256'][:16]}…`")
        lines.append("")
    return "\n".join(lines)
