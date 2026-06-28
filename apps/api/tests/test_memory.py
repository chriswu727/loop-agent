"""Cross-task memory store."""

from __future__ import annotations

from pathlib import Path

from app.services.memory import MemoryStore


def test_remember_appears_in_snapshot(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem")
    assert store.snapshot() == ""  # empty to start
    store.remember("The user prefers tabs over spaces")
    assert "tabs over spaces" in store.snapshot()


def test_topic_notes_are_grouped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store.remember("API base url is https://api.example.com", topic="project")
    snap = store.snapshot()
    assert "## project" in snap
    assert "api.example.com" in snap


def test_empty_note_is_ignored(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store.remember("   ")
    assert store.snapshot() == ""


def test_snapshot_is_bounded(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem")
    for i in range(1000):
        store.remember(f"fact number {i}")
    snap = store.snapshot(limit=500)
    assert len(snap) <= 500
    assert "fact number 999" in snap  # keeps the most recent
