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


async def test_command_check_can_require_any_nonzero_exit(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    results = await run_checks(
        [
            {
                "kind": "command",
                "command": "python3 -c 'raise SystemExit(3)'",
                "expect_exit": "nonzero",
            },
            {
                "kind": "command",
                "command": "python3 -c 'raise SystemExit(0)'",
                "expect_exit": "nonzero",
            },
        ],
        ws,
    )

    assert [result.passed for result in results] == [True, False]


async def test_command_check_rejects_zero_discovered_tests(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    results = await run_checks(
        [{"kind": "command", "command": "python3 -m unittest discover -v"}], ws
    )

    assert results[0].passed is False
    assert "zero tests" in results[0].evidence


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


async def test_each_check_gets_an_independent_workspace_copy(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    results = await run_checks(
        [
            {"kind": "command", "command": "touch generated.txt"},
            {"kind": "file_exists", "path": "generated.txt"},
        ],
        ws,
    )

    assert [result.passed for result in results] == [True, False]


def test_sweep_orphaned_verify_dirs(tmp_path: Path) -> None:
    from app.services.verification import sweep_orphaned_verify_dirs

    root = tmp_path / "ws"
    root.mkdir()
    (root / "verify-abc123").mkdir()  # orphaned crash residue
    (root / "verify-def456" / "ws").mkdir(parents=True)
    (root / "some-task-id").mkdir()  # a real task workspace — must be kept
    (root / "verify-note.txt").write_text("x")  # a file, not a dir — kept

    assert sweep_orphaned_verify_dirs(root) == 2
    assert not (root / "verify-abc123").exists()
    assert not (root / "verify-def456").exists()
    assert (root / "some-task-id").exists()  # untouched
    assert (root / "verify-note.txt").exists()
    assert sweep_orphaned_verify_dirs(tmp_path / "nonexistent") == 0  # no dir -> 0, no raise
