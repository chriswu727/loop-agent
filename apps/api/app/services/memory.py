"""Cross-task memory — what the agent carries between tasks.

A simple, file-backed store: an evergreen ``MEMORY.md`` plus optional per-topic
files under ``topics/``. A snapshot is injected into the agent's context at the
start of each task, and the agent appends to it with the ``remember`` tool. This
gives an OpenClaw-style persistent memory while staying transparent (it's just
markdown a user can read and edit) and bounded (the snapshot is size-capped).

v1 is a single shared store; per-user/per-project scoping is a later concern.
"""

from __future__ import annotations

import re
from pathlib import Path


def _safe_topic(topic: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", topic.strip().lower()).strip("-")
    return slug[:60] or "notes"


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.main = self.root / "MEMORY.md"

    def snapshot(self, *, limit: int = 4000) -> str:
        """The memory the agent sees, most-recent-first-bounded. Empty if none."""
        parts: list[str] = []
        if self.main.is_file():
            parts.append(self.main.read_text(encoding="utf-8", errors="replace"))
        topics = self.root / "topics"
        if topics.is_dir():
            for f in sorted(topics.glob("*.md")):
                parts.append(f"## {f.stem}\n" + f.read_text(encoding="utf-8", errors="replace"))
        text = "\n".join(p.strip() for p in parts if p.strip()).strip()
        # Keep the tail (most recently appended) when over the cap.
        return text[-limit:] if len(text) > limit else text

    def remember(self, note: str, topic: str | None = None) -> str:
        note = note.strip()
        if not note:
            return "Nothing to remember (empty note)."
        line = f"- {note}\n"
        if topic:
            topics = self.root / "topics"
            topics.mkdir(exist_ok=True)
            with (topics / f"{_safe_topic(topic)}.md").open("a", encoding="utf-8") as f:
                f.write(line)
        else:
            with self.main.open("a", encoding="utf-8") as f:
                f.write(line)
        return f"Remembered: {note[:120]}"
