#!/usr/bin/env python3
"""Independently verify a Loop Receipt's content hash.

Usage:  python scripts/verify_receipt.py path/to/receipt.json

Recomputes the content address from the receipt body and compares it to the
stored hash — so anyone can confirm, offline, that a Receipt hasn't been altered
since Loop signed it. Exits 0 if valid, 1 if not (drop it in a CI gate).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def canonical_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify_receipt.py <receipt.json>", file=sys.stderr)
        return 2
    try:
        receipt = json.loads(Path(argv[1]).read_text())
    except (OSError, ValueError) as exc:
        print(f"could not read receipt: {exc}", file=sys.stderr)
        return 2

    claimed = receipt.get("receipt_hash", "")
    # `signature` (if present) is added after hashing, so exclude it too.
    body = {k: v for k, v in receipt.items() if k not in ("receipt_hash", "signature")}
    recomputed = canonical_hash(body)
    hash_ok = bool(claimed) and recomputed == claimed

    # Re-hash every output file against the manifest, resolved next to the receipt,
    # so an output altered after the fact is caught — not just an inconsistent edit.
    receipt_dir = Path(argv[1]).resolve().parent
    file_mismatches: list[str] = []
    for f in receipt.get("files", []):
        p = (receipt_dir / f.get("path", "")).resolve()
        try:
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            file_mismatches.append(f"{f.get('path')} (missing)")
            continue
        if actual != f.get("sha256"):
            file_mismatches.append(f"{f.get('path')} (modified)")

    checks = receipt.get("checks") or []
    passed = sum(1 for c in checks if c.get("passed"))
    signed = "yes" if receipt.get("signature") else "no (tamper-evident only)"
    print(f"goal:        {receipt.get('goal', '?')}")
    print(f"verified by: {receipt.get('verified_by')} · isolation: {receipt.get('isolation')}")
    print(f"checks:      {passed}/{len(checks)} passed")
    print(f"signed:      {signed}")
    print(f"claimed:     {claimed}")
    print(f"recomputed:  {recomputed}")
    print(
        f"files:       {len(receipt.get('files', []))} checked, {len(file_mismatches)} mismatched"
    )
    for m in file_mismatches:
        print(f"  - {m}")
    ok = hash_ok and not file_mismatches
    print("RESULT:      OK — receipt is authentic" if ok else "RESULT:      TAMPERED — mismatch")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
