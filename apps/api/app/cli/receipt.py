from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.services.completion import attach_baseline
from app.services.prompts import verify_prompts
from app.services.receipt import verify_receipt_full
from app.services.verification import as_dicts, run_checks
from app.tools import CapabilityEnvelope, Workspace


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("receipt root must be an object")
    return value


def _public_key(path: str | None) -> str | None:
    return Path(path).read_text() if path else None


def _report(receipt: dict[str, Any], report: dict[str, Any]) -> None:
    print(f"schema:      {receipt.get('schema', 'legacy')}")
    print(f"task:        {receipt.get('task_id', '?')}")
    print(f"goal:        {receipt.get('goal', '?')}")
    print(f"verified by: {receipt.get('verified_by', '?')}")
    print(f"assurance:   {report.get('assurance', 'invalid')}")
    print(f"signature:   {report.get('signature', 'unknown')}")
    print(f"files:       {'ok' if report.get('files_ok', True) else 'modified'}")


def _definitions(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    checks = receipt.get("checks")
    definitions: list[dict[str, Any]] = []
    for check in checks if isinstance(checks, list) else []:
        if not isinstance(check, dict):
            continue
        definition = check.get("definition")
        if isinstance(definition, dict):
            definitions.append(definition)
    if definitions:
        return definitions
    contract = receipt.get("contract")
    required = contract.get("required_checks") if isinstance(contract, dict) else None
    if isinstance(required, list):
        definitions.extend(check for check in required if isinstance(check, dict))
    return definitions


def _recorded_image(receipt: dict[str, Any]) -> str | None:
    provenance = receipt.get("provenance")
    sandbox = provenance.get("sandbox") if isinstance(provenance, dict) else None
    if not isinstance(sandbox, dict):
        return None
    image = sandbox.get("image")
    digest = sandbox.get("image_digest")
    if not isinstance(image, str) or not image:
        return None
    if isinstance(digest, str) and digest:
        base = image.split("@", 1)[0]
        return f"{base}@{digest}"
    return image


async def _replay(
    receipt: dict[str, Any],
    workspace: Workspace,
    *,
    image: str | None,
    allow_host: bool,
) -> list[dict[str, Any]]:
    definitions = _definitions(receipt)
    if not definitions:
        raise ValueError("receipt contains no replayable check definitions")
    has_commands = any(check.get("kind") == "command" for check in definitions)
    sandbox_image = image or _recorded_image(receipt)
    if has_commands and sandbox_image is None and not allow_host:
        raise ValueError(
            "command replay requires a recorded/passed sandbox image; "
            "use --allow-host only in an already isolated environment"
        )
    authority = receipt.get("authority")
    resolved = authority.get("resolved", []) if isinstance(authority, dict) else []
    hosts = authority.get("egress_hosts", []) if isinstance(authority, dict) else []
    envelope = CapabilityEnvelope.from_capabilities(
        resolved if isinstance(resolved, list) else [],
        egress_hosts=hosts if isinstance(hosts, list) else [],
    )
    results = attach_baseline(
        await run_checks(
            definitions,
            workspace,
            envelope=envelope,
            sandbox_image=None if allow_host and sandbox_image is None else sandbox_image,
            criterion_count=len(receipt.get("criteria") or receipt.get("rubric") or []),
        ),
        [item for item in receipt.get("baseline_checks", []) if isinstance(item, dict)],
    )
    return as_dicts(results)


async def _evaluate(receipt: dict[str, Any], workspace: Workspace) -> dict[str, Any]:
    from app.core.llm import get_verifier_client

    raw_checks = receipt.get("checks")
    checks: list[Any] = raw_checks if isinstance(raw_checks, list) else []
    raw_rubric = receipt.get("rubric")
    rubric: list[Any] = raw_rubric if isinstance(raw_rubric, list) else []
    check_text = (
        "\n".join(
            f"[{'PASS' if check.get('passed') else 'FAIL'}] "
            f"{check.get('kind')} {check.get('target')}: {check.get('evidence')}"
            for check in checks
            if isinstance(check, dict)
        )
        or "(no recorded checks)"
    )
    system, user = verify_prompts(
        str(receipt.get("goal", "")),
        [str(value) for value in rubric],
        str(receipt.get("summary") or ""),
        workspace.tree(),
        check_text,
        workspace.contents_digest(),
        today=date.today().isoformat(),
    )
    result = await get_verifier_client().complete(system, user, max_tokens=1500, temperature=0.0)
    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError:
        start, end = result.content.find("{"), result.content.rfind("}")
        parsed = json.loads(result.content[start : end + 1]) if start >= 0 < end else {}
    if not isinstance(parsed, dict):
        raise ValueError("verifier returned no structured verdict")
    parsed["provider"] = result.provider
    parsed["tokens"] = result.tokens
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop receipt")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "verify", "evaluate"):
        command = sub.add_parser(name)
        command.add_argument("receipt", type=Path)
        command.add_argument("--pubkey")
    replay = sub.add_parser("replay")
    replay.add_argument("receipt", type=Path)
    replay.add_argument("--pubkey")
    replay.add_argument("--sandbox-image")
    replay.add_argument("--allow-host", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = _load(args.receipt)
        workspace = Workspace(args.receipt.resolve().parent)
        report = verify_receipt_full(
            receipt,
            workspace=workspace,
            public_key_pem=_public_key(args.pubkey),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"receipt error: {exc}", file=sys.stderr)
        return 2

    _report(receipt, report)
    if args.command == "inspect":
        return 0
    if not report["valid"]:
        print("result:      invalid")
        return 1
    if args.command == "verify":
        print("result:      integrity verified")
        return 0
    if args.command == "evaluate":
        try:
            verdict = asyncio.run(_evaluate(receipt, workspace))
        except Exception as exc:
            print(f"evaluation failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
        return 0 if verdict.get("met") is True else 1
    try:
        results = asyncio.run(
            _replay(
                receipt,
                workspace,
                image=args.sandbox_image,
                allow_host=args.allow_host,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"replay refused: {exc}", file=sys.stderr)
        return 2
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['check_id']} {result['kind']} {result['target']}")
    replayed = bool(results) and all(
        result["passed"]
        or (result.get("source") == "system" and result.get("baseline_passed") is False)
        for result in results
    )
    print(f"result:      {'replay passed' if replayed else 'replay failed'}")
    return 0 if replayed else 1


if __name__ == "__main__":
    raise SystemExit(main())
