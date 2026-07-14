"""The Receipt is independently re-verifiable: recompute its content hash and any
tampering with a recorded fact is detected."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm.client import FallbackLLMClient
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.agent_react import AgentReactService
from app.services.receipt import _canonical_hash, verify_receipt
from app.services.task import TaskService
from app.services.verification import CheckResult
from app.tools import Workspace


def test_verify_receipt_detects_tampering() -> None:
    body = {"goal": "x", "score": 90, "checks": [{"passed": True}]}
    receipt = {"receipt_hash": _canonical_hash(body), **body}
    assert verify_receipt(receipt)[0] is True
    receipt["score"] = 100  # tamper with a recorded fact
    assert verify_receipt(receipt)[0] is False


def _build(tmp_path: Path, **overrides: object):
    from unittest.mock import MagicMock

    from app.services.receipt import build_receipt
    from app.tools import Workspace

    ws = Workspace(tmp_path / "ws")
    ws.write("out.txt", "hello world")
    task = MagicMock(
        **{
            "id": "t1",
            "goal": "do a thing",
            "rubric": ["make out.txt"],
            "sandbox": "inline",
            "steps_used": 3,
            "tokens_used": 99,
            **overrides,
        }
    )
    h, receipt = build_receipt(
        task, [], score=90, verified_by="judgment", workspace=ws, ledger_head="abc"
    )
    return ws, h, receipt


def test_kubernetes_receipt_records_the_sandbox_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "agent_sandbox_image", "registry.example/loop@sha256:abc")
    _, _, receipt = _build(tmp_path, sandbox="kubernetes")

    assert receipt["provenance"]["sandbox"]["mode"] == "kubernetes"
    assert receipt["provenance"]["sandbox"]["image"] == "registry.example/loop@sha256:abc"


def test_verify_full_catches_a_modified_output_file(tmp_path: Path) -> None:
    from app.services.receipt import verify_receipt_full

    ws, h, receipt = _build(tmp_path)
    assert verify_receipt_full(receipt, workspace=ws, db_anchor=h)["valid"] is True
    (tmp_path / "ws" / "out.txt").write_text("HACKED")  # alter the output after the fact
    report = verify_receipt_full(receipt, workspace=ws, db_anchor=h)
    assert report["files_ok"] is False
    assert report["valid"] is False
    assert report["file_mismatches"][0]["path"] == "out.txt"


def test_verify_full_catches_forged_fact_via_db_anchor(tmp_path: Path) -> None:
    # Edit a fact AND recompute the embedded hash so it is self-consistent — the
    # independent DB anchor still catches it.
    from app.services.receipt import _NON_BODY_KEYS, verify_receipt_full

    ws, h, receipt = _build(tmp_path)
    forged = dict(receipt)
    forged["goal"] = "something the agent never did"
    forged["receipt_hash"] = _canonical_hash(
        {k: v for k, v in forged.items() if k not in _NON_BODY_KEYS}
    )
    report = verify_receipt_full(forged, workspace=ws, db_anchor=h)  # h = original hash
    assert report["hash_ok"] is True  # self-consistent...
    assert report["anchor_ok"] is False  # ...but the anchor exposes the forgery
    assert report["valid"] is False


def test_signed_receipt_verifies_and_forgery_is_signature_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.services.receipt import _NON_BODY_KEYS, verify_receipt_full

    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()).decode()
    monkeypatch.setattr(settings, "agent_receipt_signing_key", pem)

    ws, h, receipt = _build(tmp_path)
    assert receipt.get("signature")  # it was signed
    assert verify_receipt_full(receipt, workspace=ws, db_anchor=h)["signature"] == "valid"

    forged = dict(receipt)
    forged["score"] = 100
    forged["receipt_hash"] = _canonical_hash(
        {k: v for k, v in forged.items() if k not in _NON_BODY_KEYS}
    )
    # The forger recomputed the hash but can't re-sign it without the private key.
    assert verify_receipt_full(forged, workspace=ws)["signature"] == "invalid"


async def test_receipt_roundtrip_produces_and_verifies(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "agent_sandbox", "inline")
    monkeypatch.setattr(settings, "agent_workspaces_root", str(tmp_path / "ws"))
    monkeypatch.setattr(settings, "agent_memory_root", str(tmp_path / "mem"))

    repo = TaskRepository(session)
    task = await repo.create(
        goal="demo",
        status="pending",
        rubric=[],
        max_steps=8,
        token_budget=1_000_000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await session.commit()
    await AgentReactService(repo, StepRepository(session), FallbackLLMClient(primary="mock")).run(
        task.id
    )

    service = TaskService(repo, StepRepository(session))
    receipt = await service.get_receipt(task.id)
    assert receipt is not None
    assert verify_receipt(receipt)[0] is True  # the real produced Receipt verifies
    receipt["goal"] = "not what actually ran"
    assert verify_receipt(receipt)[0] is False


async def test_receipt_replay_checks_integrity_before_execution(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.exceptions import ConflictError
    from app.services.receipt import build_receipt

    monkeypatch.setattr(settings, "agent_sandbox", "off")
    repo = TaskRepository(session)
    task = await repo.create(
        goal="produce a checked file",
        status="completed",
        rubric=["result exists"],
        resolved_capabilities=["fs.read"],
        max_steps=8,
        token_budget=10_000,
        summary="done",
        verification_score=100,
        steps_used=1,
        tokens_used=10,
        workspace_path=None,
        sandbox="inline",
    )
    workspace = Workspace(tmp_path / str(task.id))
    workspace.write("result.txt", "verified")
    task.workspace_path = str(workspace.root)
    receipt_hash, receipt = build_receipt(
        task,
        [
            CheckResult(
                "file_exists",
                "result.txt",
                True,
                "found",
                check_id="check-001",
                criterion_ids=("criterion-001",),
                definition={"kind": "file_exists", "path": "result.txt"},
            )
        ],
        score=100,
        verified_by="execution",
        workspace=workspace,
    )
    task.receipt_hash = receipt_hash
    await session.commit()
    service = TaskService(repo, StepRepository(session))

    replayed = await service.replay_receipt(task.id)
    assert replayed["passed"] is True

    receipt["goal"] = "tampered"
    workspace.write("receipt.json", json.dumps(receipt))
    with pytest.raises(ConflictError, match="integrity"):
        await service.replay_receipt(task.id)


def test_verify_receipt_script_stays_in_sync_with_library(tmp_path: Path) -> None:
    # scripts/verify_receipt.py duplicates the canonical-hash algorithm so it can
    # verify a Receipt with zero app dependencies. Guard the two staying in sync:
    # run the actual script on a genuine (unverified) Receipt — it must pass — and
    # on a tampered one — it must fail. Otherwise a change to _canonical_hash would
    # silently break the advertised `make verify-receipt`.
    import json
    import subprocess
    import sys
    from unittest.mock import MagicMock

    from app.services.receipt import build_receipt
    from app.tools import Workspace

    ws = Workspace(tmp_path / "ws")
    ws.write("out.txt", "hello")
    task = MagicMock(
        id="t1", goal="do a thing", rubric=[], sandbox="inline", steps_used=3, tokens_used=99
    )
    build_receipt(task, [], score=0, verified_by="unverified", workspace=ws, ledger_head="abc")
    rp = tmp_path / "ws" / "receipt.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_receipt.py"

    assert subprocess.run([sys.executable, str(script), str(rp)]).returncode == 0

    d = json.loads(rp.read_text())
    d["goal"] = "tampered after signing"
    rp.write_text(json.dumps(d))
    assert subprocess.run([sys.executable, str(script), str(rp)]).returncode == 1


async def test_receipt_endpoint_404_without_receipt(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "no receipt yet"})).json()
    resp = await client.get(f"/api/v1/tasks/{created['id']}/receipt")
    assert resp.status_code == 404


def test_verify_receipt_full_never_raises_on_malformed_receipt(tmp_path: Path) -> None:
    # receipt.json is workspace-writable; a malformed shape must return invalid, not 500.
    from app.services.receipt import verify_receipt_full
    from app.tools import Workspace

    ws = Workspace(tmp_path / "ws")
    for bad in (
        {"receipt_hash": "ab", "signature": 123, "files": []},
        {"receipt_hash": "ab", "files": [{"sha256": "x"}]},  # missing path
        {"receipt_hash": "ab", "files": ["oops"]},  # non-dict entry
    ):
        report = verify_receipt_full(bad, workspace=ws)  # must not raise
        assert report["valid"] is False


def test_offline_verifier_confines_manifest_paths(tmp_path: Path) -> None:
    # A crafted manifest path must not make the script read outside the receipt dir.
    import subprocess
    import sys

    rp = tmp_path / "receipt.json"
    body = {"goal": "x", "files": [{"path": "/etc/hosts", "sha256": "0" * 64}]}
    from app.services.receipt import _canonical_hash

    rp.write_text(json.dumps({"receipt_hash": _canonical_hash(body), **body}))
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_receipt.py"
    out = subprocess.run([sys.executable, str(script), str(rp)], capture_output=True, text=True)
    assert out.returncode == 1
    assert "escapes receipt dir" in out.stdout


def test_offline_verifier_checks_signature_with_pubkey(tmp_path: Path) -> None:
    # A forged receipt with a junk signature must fail when --pubkey is supplied.
    import subprocess
    import sys

    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = (
        priv.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo).decode()
    )
    _ws, _h, receipt = _build(tmp_path)  # unsigned
    receipt["signature"] = "00" * 64  # forged signature
    rp = tmp_path / "ws" / "receipt.json"
    rp.write_text(json.dumps(receipt))
    pk = tmp_path / "pub.pem"
    pk.write_text(pub)
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_receipt.py"

    out = subprocess.run(
        [sys.executable, str(script), str(rp), "--pubkey", str(pk)], capture_output=True, text=True
    )
    assert out.returncode == 1  # signature INVALID
    assert "INVALID" in out.stdout
