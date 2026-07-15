from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import fakeredis

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "enforcement_acceptance.py"


def test_enforcement_harness_crosses_process_and_tcp_boundaries() -> None:
    server = fakeredis.TcpFakeServer(
        ("127.0.0.1", 0),
        server_type="redis",
        server_version=(7, 0),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    common = [
        "--redis-url",
        f"redis://127.0.0.1:{port}/15",
        "--namespace",
        "loop:acceptance:test",
    ]
    try:
        exercised = subprocess.run(
            [sys.executable, str(SCRIPT), "exercise", *common],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert exercised.returncode == 0, exercised.stderr
        assert json.loads(exercised.stdout)["cross_process_disconnect"] is True

        recovered = subprocess.run(
            [sys.executable, str(SCRIPT), "verify-recovery", *common],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert recovered.returncode == 0, recovered.stderr
        assert json.loads(recovered.stdout)["revocation_persisted"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
