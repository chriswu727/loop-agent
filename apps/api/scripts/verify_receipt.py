#!/usr/bin/env python3
"""Independently verify a Loop Receipt — offline, dependency-light.

Usage:  python scripts/verify_receipt.py path/to/receipt.json [--pubkey key.pem]

Recomputes the content address from the receipt body, re-hashes every output file
against the manifest, and — if you pass the publisher's ed25519 public key with
``--pubkey`` — verifies the detached signature (needs the ``cryptography`` package).

Without ``--pubkey`` it reports content-hash + file integrity only and says so; it
does NOT claim a signature is authentic just because one is present. Exits 0 if the
performed checks pass, 1 on any mismatch/tampering, 2 on a usage/read error. Drop it
in a CI gate (pass --pubkey there so the signature is actually enforced).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def canonical_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_file(receipt_dir: Path, rel: object) -> tuple[Path | None, str | None]:
    """Resolve a manifest path *inside* the receipt's own directory. A crafted
    receipt must not make us read /etc/passwd or ../../secrets via its manifest."""
    if not isinstance(rel, str) or not rel:
        return None, "malformed"
    if rel.startswith("/") or ".." in Path(rel).parts:
        return None, "escapes receipt dir"
    p = (receipt_dir / rel).resolve()
    if receipt_dir != p and receipt_dir not in p.parents:
        return None, "escapes receipt dir"
    if not p.is_file():
        return None, "missing"
    return p, None


def _signature_status(receipt: dict, pubkey_pem: str | None) -> str:
    sig = receipt.get("signature")
    if not sig:
        return "unsigned"
    if not isinstance(sig, str):
        return "INVALID"
    if not pubkey_pem:
        return "present-but-UNCHECKED (pass --pubkey to verify)"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        return "present-but-UNCHECKED (install 'cryptography' to verify)"
    try:
        key = load_pem_public_key(pubkey_pem.encode())
        if not isinstance(key, Ed25519PublicKey):
            return "INVALID"
        key.verify(bytes.fromhex(sig), str(receipt.get("receipt_hash", "")).encode())
        return "VERIFIED"
    except Exception:
        return "INVALID"


def main(argv: list[str]) -> int:
    args = argv[1:]
    pubkey_pem: str | None = None
    if "--pubkey" in args:
        i = args.index("--pubkey")
        try:
            pubkey_pem = Path(args[i + 1]).read_text()
        except (OSError, IndexError) as exc:
            print(f"could not read --pubkey: {exc}", file=sys.stderr)
            return 2
        args = args[:i] + args[i + 2 :]
    if len(args) != 1:
        print("usage: verify_receipt.py <receipt.json> [--pubkey key.pem]", file=sys.stderr)
        return 2
    try:
        receipt = json.loads(Path(args[0]).read_text())
    except (OSError, ValueError) as exc:
        print(f"could not read receipt: {exc}", file=sys.stderr)
        return 2

    claimed = receipt.get("receipt_hash", "")
    body = {k: v for k, v in receipt.items() if k not in ("receipt_hash", "signature")}
    recomputed = canonical_hash(body)
    hash_ok = bool(claimed) and recomputed == claimed

    receipt_dir = Path(args[0]).resolve().parent
    file_mismatches: list[str] = []
    manifest = receipt.get("files") or []
    for f in manifest if isinstance(manifest, list) else []:
        rel = f.get("path") if isinstance(f, dict) else f
        p, err = _safe_file(receipt_dir, rel)
        if p is None:
            file_mismatches.append(f"{rel} ({err})")
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != f.get("sha256"):
            file_mismatches.append(f"{rel} (modified)")

    sig_status = _signature_status(receipt, pubkey_pem)
    checks = receipt.get("checks") or []
    passed = sum(1 for c in checks if isinstance(c, dict) and c.get("passed"))
    print(f"goal:        {receipt.get('goal', '?')}")
    print(f"verified by: {receipt.get('verified_by')} · isolation: {receipt.get('isolation')}")
    print(f"checks:      {passed}/{len(checks)} passed")
    print(f"signature:   {sig_status}")
    print(f"claimed:     {claimed}")
    print(f"recomputed:  {recomputed}")
    print(f"files:       {len(manifest)} checked, {len(file_mismatches)} mismatched")
    for m in file_mismatches:
        print(f"  - {m}")

    ok = hash_ok and not file_mismatches and sig_status != "INVALID"
    if ok and "UNCHECKED" in sig_status:
        print("RESULT:      OK — content hash + files intact (signature NOT verified)")
    elif ok:
        print("RESULT:      OK — receipt is authentic")
    else:
        print("RESULT:      TAMPERED — mismatch")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
