#!/usr/bin/env python3
"""Create and sign Loop skills.

  python scripts/skill_tool.py keygen [OUT_DIR]     # signing_key.pem + trust_key.pem
  python scripts/skill_tool.py sign SKILL_DIR KEY   # writes SKILL_DIR/skill.json.sig

Loop only ever *verifies* signatures at runtime (against AGENT_SKILL_TRUST_*);
signing is a build/author step, which is what this tool is for. Keep the private
signing key secret; publish/point the trust key at trust_key.pem.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.services.skills import generate_keypair, sign_skill


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "keygen":
        out = Path(argv[2]) if len(argv) > 2 else Path(".")
        out.mkdir(parents=True, exist_ok=True)
        private_pem, public_pem = generate_keypair()
        (out / "signing_key.pem").write_text(private_pem)
        (out / "trust_key.pem").write_text(public_pem)
        print(f"wrote {out}/signing_key.pem  (KEEP SECRET — signs skills)")
        print(f"wrote {out}/trust_key.pem    (the trust root — AGENT_SKILL_TRUST_PUBLIC_KEY_FILE)")
        return 0

    if len(argv) == 4 and argv[1] == "sign":
        skill_dir, key = Path(argv[2]), Path(argv[3])
        sign_skill(skill_dir, key.read_text())
        print(f"signed {skill_dir}/skill.json -> {skill_dir}/skill.json.sig")
        return 0

    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
