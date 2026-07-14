from __future__ import annotations

import sys

from app.cli.receipt import main as receipt_main


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "receipt":
        return receipt_main(sys.argv[2:])
    print("usage: loop receipt {inspect,verify,replay,evaluate} ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
