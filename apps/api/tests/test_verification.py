"""Re-execution verification: checks must actually run and gate acceptance."""

from __future__ import annotations

from pathlib import Path

from app.services.verification import run_checks
from app.tools.workspace import Workspace


async def test_command_check_passes_on_zero_exit(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("hi.py", "print('hello')")
    results = await run_checks(
        [{"kind": "command", "command": "python3 hi.py", "expect_stdout": "hello"}], ws
    )
    assert len(results) == 1
    assert results[0].passed is True


async def test_command_check_fails_on_wrong_output(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("hi.py", "print('hello')")
    results = await run_checks(
        [{"kind": "command", "command": "python3 hi.py", "expect_stdout": "goodbye"}], ws
    )
    assert results[0].passed is False


async def test_file_checks(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("data.csv", "a,b\n1,2\n")
    results = await run_checks(
        [
            {"kind": "file_exists", "path": "data.csv"},
            {"kind": "file_exists", "path": "nope.csv"},
            {"kind": "file_contains", "path": "data.csv", "text": "a,b"},
            {"kind": "file_contains", "path": "data.csv", "text": "zzz"},
        ],
        ws,
    )
    assert [r.passed for r in results] == [True, False, True, False]


async def test_checks_run_on_a_copy_not_the_original(tmp_path: Path) -> None:
    # A check that writes a file must not leave anything in the real workspace.
    ws = Workspace(tmp_path / "ws")
    await run_checks([{"kind": "command", "command": "touch sentinel.txt"}], ws)
    assert ("sentinel.txt", 0) not in ws.list_files()
