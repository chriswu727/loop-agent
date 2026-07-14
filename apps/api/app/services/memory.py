"""Cross-task memory — what the agent carries between tasks.

A simple, file-backed store: an evergreen ``MEMORY.md`` plus optional per-topic
files under ``topics/``. A snapshot is injected into the agent's context at the
start of each task, and the agent appends to it with the ``remember`` tool. This
gives an OpenClaw-style persistent memory while staying transparent (it's just
markdown a user can read and edit) and bounded (the snapshot is size-capped).

Each owner/project gets a separate transparent file-backed store.
"""

from __future__ import annotations

import re
from pathlib import Path


def _safe_topic(topic: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", topic.strip().lower()).strip("-")
    return slug[:60] or "notes"


def scoped_memory_root(root: Path, owner_id: str, project_id: str) -> Path:
    def component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:100] or "default"

    return root / component(owner_id) / component(project_id)


_MAX_NOTE = 1000  # a memory note is a concise fact, not a document
_MAX_FILE_BYTES = 32_000  # keep each memory file bounded so startup reads stay cheap


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
        truncated = len(note) > _MAX_NOTE
        if truncated:
            note = note[:_MAX_NOTE].rstrip() + "…"
        if topic:
            (self.root / "topics").mkdir(exist_ok=True)
            target = self.root / "topics" / f"{_safe_topic(topic)}.md"
        else:
            target = self.main
        with target.open("a", encoding="utf-8") as f:
            f.write(f"- {note}\n")
        self._trim(target)
        return f"Remembered: {note[:120]}" + (" (note was truncated)" if truncated else "")

    @staticmethod
    def _trim(path: Path) -> None:
        """Bound each memory file (tail-most, line-aligned) so a task can't grow the
        shared store without limit and inflate every future task's startup read."""
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if len(data) <= _MAX_FILE_BYTES:
            return
        tail = data[-_MAX_FILE_BYTES:]
        nl = tail.find("\n")  # drop the leading partial line so entries stay intact
        path.write_text(tail[nl + 1 :] if nl != -1 else tail, encoding="utf-8")
