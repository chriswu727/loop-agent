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
import platform
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from app.core.config import settings
from app.db.models.task import TaskModel
from app.services.completion import completion_gates_pass
from app.services.verification import CheckResult, as_dicts
from app.tools import Workspace

RECEIPT_JSON = "receipt.json"
RECEIPT_MD = "RECEIPT.md"
RECEIPT_SCHEMA = "loop.receipt/v1"
# Added AFTER the content hash is computed, so they're excluded when recomputing it.
_NON_BODY_KEYS = ("receipt_hash", "signature")


def _signing_key() -> Ed25519PrivateKey | None:
    pem = settings.receipt_signing_key_pem()
    if not pem:
        return None
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        return None
    return key if isinstance(key, Ed25519PrivateKey) else None


def _verify_key() -> Ed25519PublicKey | None:
    """The public key Receipt signatures verify against — derived from the server's
    signing key. (Independent verifiers use the published public key out-of-band.)"""
    priv = _signing_key()
    return priv.public_key() if priv is not None else None


def _signing_key_id(key: Ed25519PrivateKey) -> str:
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"ed25519:{hashlib.sha256(public).hexdigest()[:24]}"


def _file_manifest(
    workspace: Workspace, *, include_paths: list[str] | None = None
) -> list[dict[str, Any]]:
    """sha256 + size of every workspace file, excluding the receipt itself."""
    manifest: list[dict[str, Any]] = []
    files = (
        [(path, workspace.resolve(path).stat().st_size) for path in include_paths]
        if include_paths is not None
        else workspace.list_files()
    )
    for rel, size in files:
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
    attempt = task.attempt if isinstance(task.attempt, int) else 1
    authority_schema = (
        task.authority_schema if isinstance(task.authority_schema, str) else "loop.capabilities/v1"
    )
    requested_capabilities = (
        task.requested_capabilities
        if isinstance(task.requested_capabilities, list) or task.requested_capabilities is None
        else None
    )
    resolved_capabilities = (
        task.resolved_capabilities if isinstance(task.resolved_capabilities, list) else []
    )
    egress_hosts = task.egress_hosts if isinstance(task.egress_hosts, list) else []
    summary = task.summary if isinstance(task.summary, str) or task.summary is None else None
    criteria_source = task.criteria_source if isinstance(task.criteria_source, str) else "generated"
    verification_mode = (
        task.verification_mode if isinstance(task.verification_mode, str) else "judgment"
    )
    required_checks = task.required_checks if isinstance(task.required_checks, list) else []
    baseline_checks = task.baseline_checks if isinstance(task.baseline_checks, list) else []
    contract_status = (
        task.contract_status if isinstance(task.contract_status, str) else "not_required"
    )
    contract_hash = task.contract_hash if isinstance(task.contract_hash, str) else None
    contract_draft = task.contract_draft if isinstance(task.contract_draft, dict) else None
    executor_models = task.executor_models if isinstance(task.executor_models, list) else []
    verifier_model = task.verifier_model if isinstance(task.verifier_model, dict) else None
    criteria = [
        {"id": f"criterion-{index:03d}", "text": criterion}
        for index, criterion in enumerate(task.rubric or [], start=1)
    ]
    covered_criteria = sorted(
        {criterion for check in checks for criterion in check.get("criterion_ids", [])}
    )
    change_set: dict[str, Any] | None = None
    manifest_paths: list[str] | None = None
    project_base_commit = task.project_base_commit
    if isinstance(project_base_commit, str) and project_base_commit:
        from app.services.changeset import inspect_changes

        snapshot = inspect_changes(workspace.root, project_base_commit)
        change_set = {
            "schema": "loop.changeset/v1",
            "base_commit": project_base_commit,
            "patch_sha256": snapshot.patch_sha256,
            "files": [
                {
                    "path": change.path,
                    "previous_path": change.previous_path,
                    "status": change.status,
                    "additions": change.additions,
                    "deletions": change.deletions,
                }
                for change in snapshot.files
            ],
        }
        manifest_paths = [
            change.path
            for change in snapshot.files
            if change.status[:1] != "D" and workspace.resolve(change.path).is_file()
        ]
    body: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "issued_at": datetime.now(UTC).isoformat(),
        "task_id": str(task.id),
        "attempt": attempt,
        "goal": task.goal,
        "summary": summary,
        "rubric": task.rubric or [],
        "criteria": criteria,
        "contract": {
            "criteria_source": criteria_source,
            "verification_mode": verification_mode,
            "required_checks": required_checks,
            "status": contract_status,
            "hash": contract_hash,
            "draft": contract_draft,
        },
        "verified_by": verified_by,  # "execution" | "judgment"
        "isolation": task.sandbox or "inline",  # "container" | "inline"
        "score": score,
        "checks": checks,
        "baseline_checks": baseline_checks,
        "checks_passed": (
            completion_gates_pass(check_results) if checks else (False if required_checks else None)
        ),
        # Honest coverage: how many success criteria vs how many machine checks,
        # and whether "done" rests on re-execution or on LLM judgment.
        "coverage": {
            "rubric_criteria": len(task.rubric or []),
            "checks": len(checks),
            "execution_backed": verified_by == "execution",
            "covered_criteria": covered_criteria,
        },
        "steps_used": task.steps_used,
        "tokens_used": task.tokens_used,
        # Head of the tamper-evident step chain — this Receipt vouches for the
        # entire history that produced it.
        "ledger_head": ledger_head,
        "files": _file_manifest(workspace, include_paths=manifest_paths),
        "authority": {
            "schema": authority_schema,
            "requested": requested_capabilities,
            "resolved": resolved_capabilities,
            "egress_hosts": egress_hosts,
            "audit": task.authority_audit if isinstance(task.authority_audit, list) else [],
            "enforcement": {
                "provider_gateway": bool(
                    settings.agent_provider_gateway_url
                    or settings.agent_email_gateway_url
                    or settings.agent_calendar_gateway_url
                    or settings.agent_vision_gateway_url
                ),
                "browser_gateway": bool(settings.agent_browser_gateway_url),
                "email_gateway": bool(settings.agent_email_gateway_url),
                "calendar_gateway": bool(settings.agent_calendar_gateway_url),
                "vision_gateway": bool(settings.agent_vision_gateway_url),
                "egress_proxy": bool(settings.agent_egress_proxy_url),
            },
        },
        "provenance": {
            "producer": {"name": "loop-agent", "version": settings.version},
            "revision": settings.loop_revision,
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": sys.platform,
            },
            "model": executor_models[-1] if executor_models else None,
            "executor_models": executor_models,
            "verifier": verifier_model,
            "sandbox": {
                "mode": task.sandbox or "inline",
                "image": (
                    settings.agent_sandbox_image
                    if task.sandbox in {"container", "kubernetes"}
                    else None
                ),
                "image_digest": settings.agent_sandbox_image_digest,
            },
        },
    }
    if change_set is not None:
        body["change_set"] = change_set
    product_session_id = (
        task.product_session_id if isinstance(task.product_session_id, uuid.UUID) else None
    )
    product_revision = task.product_revision if isinstance(task.product_revision, int) else None
    if product_session_id and product_revision and isinstance(task.product_specification, dict):
        body["product_revision"] = {
            "session_id": str(product_session_id),
            "revision": product_revision,
            "previous_task_id": (
                str(task.previous_revision_id)
                if isinstance(task.previous_revision_id, uuid.UUID)
                else None
            ),
            "feedback_kind": task.feedback_kind if isinstance(task.feedback_kind, str) else None,
            "feedback_delta": task.feedback_delta if isinstance(task.feedback_delta, str) else None,
            "specification": task.product_specification,
            "specification_hash": (
                task.specification_hash if isinstance(task.specification_hash, str) else None
            ),
        }
    signer = _signing_key()
    if signer is not None:
        body["signature_key_id"] = _signing_key_id(signer)
    receipt_hash = _canonical_hash(body)
    receipt = {"receipt_hash": receipt_hash, **body}
    # Optionally sign the hash: with a signing key, forging a consistent Receipt
    # needs the private key, not just workspace write access (tamper-PROOF).
    if signer is not None:
        receipt["signature"] = signer.sign(receipt_hash.encode()).hex()

    workspace.write(RECEIPT_JSON, json.dumps(receipt, indent=2, ensure_ascii=False))
    workspace.write(RECEIPT_MD, _render_markdown(receipt))
    return receipt_hash, receipt


def refresh_receipt_authority(workspace: Workspace, authority_audit: list[dict[str, Any]]) -> str:
    receipt = json.loads(workspace.read(RECEIPT_JSON))
    if not isinstance(receipt, dict) or not isinstance(receipt.get("authority"), dict):
        raise ValueError("Receipt authority record is malformed")
    receipt["authority"]["audit"] = authority_audit
    signer = _signing_key()
    if signer is not None:
        receipt["signature_key_id"] = _signing_key_id(signer)
    else:
        receipt.pop("signature_key_id", None)
    body = {key: value for key, value in receipt.items() if key not in _NON_BODY_KEYS}
    receipt_hash = _canonical_hash(body)
    receipt["receipt_hash"] = receipt_hash
    if signer is not None:
        receipt["signature"] = signer.sign(receipt_hash.encode()).hex()
    else:
        receipt.pop("signature", None)
    workspace.write(RECEIPT_JSON, json.dumps(receipt, indent=2, ensure_ascii=False))
    workspace.write(RECEIPT_MD, _render_markdown(receipt))
    return receipt_hash


def _canonical_hash(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_receipt(receipt: dict[str, Any]) -> tuple[bool, str]:
    """Recompute a receipt's content hash from its body and compare to the stored
    one. Returns (ok, recomputed_hash). Any tampering with a recorded fact — the
    goal, a check verdict, a file's sha256, the ledger head — changes the hash."""
    body = {k: v for k, v in receipt.items() if k not in _NON_BODY_KEYS}
    recomputed = _canonical_hash(body)
    return recomputed == receipt.get("receipt_hash"), recomputed


def verify_receipt_full(
    receipt: dict[str, Any],
    *,
    workspace: Workspace | None = None,
    db_anchor: str | None = None,
    public_key_pem: str | None = None,
) -> dict[str, Any]:
    """A layered re-verification, not just the self-consistent content hash:

    - ``hash_ok``    — the body still hashes to the stored ``receipt_hash``.
    - ``signature``  — unsigned | valid | invalid | unverifiable (signed but no key).
    - ``anchor_ok``  — the hash matches the one recorded independently in the DB at
      completion time, so editing the file + recomputing its own hash is caught.
    - ``files_ok``   — every output file still hashes to its recorded manifest sha256,
      so an output altered after the fact is caught.
    - ``valid``      — all performed checks passed (a bad signature or a mismatch fails).
    """
    hash_ok, recomputed = verify_receipt(receipt)
    result: dict[str, Any] = {"hash_ok": hash_ok, "recomputed_hash": recomputed}

    # receipt.json is workspace-writable, so every field is untrusted — a malformed
    # shape must return "invalid", never raise (which would 500 the endpoint).
    sig = receipt.get("signature")
    if not sig:
        result["signature"] = "unsigned"
    elif not isinstance(sig, str):
        result["signature"] = "invalid"
    else:
        vk = _verify_key()
        if public_key_pem:
            try:
                candidate = load_pem_public_key(public_key_pem.encode())
                vk = candidate if isinstance(candidate, Ed25519PublicKey) else None
            except (ValueError, TypeError):
                vk = None
        if vk is None:
            result["signature"] = "unverifiable"  # signed, but this server has no key
        else:
            try:
                vk.verify(bytes.fromhex(sig), str(receipt.get("receipt_hash", "")).encode())
                result["signature"] = "valid"
            except (InvalidSignature, ValueError, TypeError):
                result["signature"] = "invalid"

    if db_anchor is not None:
        result["anchor_ok"] = db_anchor == receipt.get("receipt_hash")

    if workspace is not None:
        mismatches: list[dict[str, str]] = []
        files = receipt.get("files")
        for f in files if isinstance(files, list) else []:
            if not isinstance(f, dict):
                mismatches.append({"path": str(f), "reason": "malformed"})
                continue
            path = f.get("path")
            if not isinstance(path, str):
                mismatches.append({"path": str(path), "reason": "malformed"})
                continue
            try:
                actual = hashlib.sha256(workspace.resolve(path).read_bytes()).hexdigest()
            except Exception:
                mismatches.append({"path": path, "reason": "missing"})
                continue
            if actual != f.get("sha256"):
                mismatches.append({"path": path, "reason": "modified"})
        result["files_ok"] = not mismatches
        result["file_mismatches"] = mismatches

    result["valid"] = bool(
        hash_ok
        and result["signature"] != "invalid"
        and result.get("anchor_ok", True)
        and result.get("files_ok", True)
    )
    result["authentic"] = bool(result["valid"] and result["signature"] == "valid")
    result["assurance"] = (
        "authentic" if result["authentic"] else ("integrity" if result["valid"] else "invalid")
    )
    return result


def _render_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# Task Receipt",
        "",
        f"- **Schema:** `{receipt.get('schema', 'legacy')}`",
        f"- **Verified by:** {receipt['verified_by']}"
        + {
            "execution": "",
            "judgment": " — _not re-executed; graded by judgment_",
            "unverified": " — _not verified; the task stopped before an accepted result_",
        }.get(receipt["verified_by"], ""),
        f"- **Isolation:** {receipt.get('isolation', 'inline')}"
        + (
            " — _commands jailed in an ephemeral container_"
            if receipt.get("isolation") == "container"
            else ""
        ),
        f"- **Score:** {receipt['score']}/100",
        (
            lambda c: (
                f"- **Coverage:** {c.get('checks', 0)} machine check(s) for "
                f"{c.get('rubric_criteria', 0)} criteria — "
                f"{'execution-backed' if c.get('execution_backed') else 'judgment'}"
            )
        )(receipt.get("coverage") or {}),
        f"- **Steps:** {receipt['steps_used']} · **Tokens:** {receipt['tokens_used']}",
        f"- **Ledger head:** `{receipt['ledger_head']}`",
        f"- **Authority:** {', '.join(receipt.get('authority', {}).get('resolved', [])) or 'none'}",
        f"- **Receipt hash:** `{receipt['receipt_hash']}`"
        + (
            " — _ed25519-signed_"
            if receipt.get("signature")
            else " — _unsigned (tamper-evident, not tamper-proof)_"
        ),
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
            baseline = (
                " — baseline PASS"
                if c.get("baseline_passed") is True
                else (
                    " — baseline FAIL (pre-existing)" if c.get("baseline_passed") is False else ""
                )
            )
            lines.append(
                f"- [{mark}] `{c.get('source', 'agent')}:{c['kind']}` "
                f"{c['target']} — {c['evidence']}{baseline}"
            )
        lines.append("")
    if receipt["files"]:
        lines.append("## Output files")
        for f in receipt["files"]:
            lines.append(f"- `{f['path']}` ({f['size']} b) sha256 `{f['sha256'][:16]}…`")
        lines.append("")
    return "\n".join(lines)
