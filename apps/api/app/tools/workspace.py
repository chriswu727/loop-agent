"""The workspace sandbox: every file the agent touches is confined here.

A task gets one workspace directory. All paths the agent supplies are resolved
*inside* it, and anything that would escape (``..``, an absolute path, a symlink
out) is refused. This is the file-side half of "guardrails, not a jail": the
agent works in its own folder and cannot scribble over the rest of the machine
through the file tools. (Shell commands are a separate, looser surface — see
``policy.py``.)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.tools.base import ToolError

MAX_FILE_BYTES = 1_000_000  # refuse to write absurdly large files
IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}


def _preview(content: str, *, max_lines: int = 20, max_chars: int = 1000) -> str:
    """A bounded echo of written content — enough to confirm the write."""
    snippet = "\n".join(content.splitlines()[:max_lines])
    truncated = len(snippet) > max_chars or snippet != content.rstrip("\n")
    return snippet[:max_chars] + ("\n… (truncated)" if truncated else "")


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        """Resolve a user-supplied path to an absolute path inside the workspace,
        or refuse if it would escape."""
        if not relative or relative.strip() in (".", "/"):
            raise ToolError("A file path is required", blocked=True)
        if os.path.isabs(relative):
            raise ToolError(
                "Absolute paths are not allowed; use a path inside the workspace", blocked=True
            )
        if ".git" in Path(relative).parts:
            raise ToolError("Git internals are not part of the task workspace", blocked=True)
        target = (self.root / relative).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError(f"Path {relative!r} escapes the workspace", blocked=True)
        return target

    def write(self, relative: str, content: str) -> str:
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ToolError("File too large to write", blocked=True)
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Echo a bounded preview so the agent can confirm the write without a
        # follow-up read_file (which just wastes a step on the file it authored).
        return f"Wrote {len(content)} chars to {relative}. Contents:\n{_preview(content)}"

    def edit(self, relative: str, old: str, new: str) -> str:
        """Replace an exact, unique snippet in a file — the agent edits instead
        of rewriting the whole thing. Refuses if ``old`` is missing or ambiguous,
        so an edit can never silently hit the wrong place."""
        target = self.resolve(relative)
        if not target.is_file():
            raise ToolError(f"No such file: {relative}")
        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            raise ToolError(f"The text to replace was not found in {relative}")
        if count > 1:
            raise ToolError(
                f"The text to replace appears {count} times in {relative}; "
                "make it unique (include more surrounding context)"
            )
        updated = text.replace(old, new, 1)
        if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
            raise ToolError("Edit would make the file too large", blocked=True)
        target.write_text(updated, encoding="utf-8")
        # Echo the result so the agent sees the edit landed without a read_file.
        return f"Edited {relative} (replaced 1 occurrence). Contents now:\n{_preview(updated)}"

    def read(self, relative: str, *, limit: int = 6000) -> str:
        target = self.resolve(relative)
        if not target.is_file():
            raise ToolError(f"No such file: {relative}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit] + f"\n... [truncated, {len(text)} chars total]"
        return text

    def list_files(self, *, max_entries: int = 500) -> list[tuple[str, int]]:
        """Every file (not directory) in the workspace as (relative_path, bytes)."""
        files: list[tuple[str, int]] = []
        for current, directories, filenames in os.walk(self.root):
            directories[:] = sorted(
                name for name in directories if name not in IGNORED_WORKSPACE_DIRS
            )
            for filename in sorted(filenames):
                path = Path(current) / filename
                if filename == ".git" or not path.is_file():
                    continue
                files.append((str(path.relative_to(self.root)), path.stat().st_size))
                if len(files) >= max_entries:
                    return files
        return files

    def state_marker(self) -> str:
        digest = hashlib.sha256()
        for relative, size in self.list_files():
            path = self.root / relative
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{relative}\0{size}\0{stat.st_mtime_ns}\n".encode())
        return digest.hexdigest()

    def contents_digest(
        self, *, max_files: int = 10, per_file: int = 2000, total: int = 8000
    ) -> str:
        """A bounded snapshot of output-file CONTENTS. The tree alone hides whether a
        file's content actually satisfies the goal, so the verifier needs this to judge
        content-only (non-executable) work by evidence rather than the agent's word.
        Skips binary and oversized files; truncates per file and in total."""
        out: list[str] = []
        used = 0
        for rel, size in self.list_files():
            if len(out) >= max_files or used >= total:
                break
            if size > 200_000:
                out.append(f"### {rel} ({size}b) — skipped (too large to show)")
                continue
            try:
                text = self.read(rel, limit=per_file)
            except Exception:
                continue
            if "\x00" in text:
                out.append(f"### {rel} ({size}b) — skipped (binary)")
                continue
            block = f"### {rel}\n{text}"
            out.append(block)
            used += len(block)
        return "\n\n".join(out) if out else "(no readable files)"

    def tree(self, *, max_entries: int = 200) -> str:
        """A compact listing of the workspace, shown to the agent each turn so it
        knows what it has already created."""
        entries: list[str] = []
        for current, directories, filenames in os.walk(self.root):
            directories[:] = sorted(
                name for name in directories if name not in IGNORED_WORKSPACE_DIRS
            )
            current_path = Path(current)
            for directory in directories:
                entries.append(f"{(current_path / directory).relative_to(self.root)}/")
                if len(entries) >= max_entries:
                    return "\n".join([*entries, "... [more]"])
            for filename in sorted(filenames):
                path = current_path / filename
                if filename == ".git" or not path.is_file():
                    continue
                entries.append(f"{path.relative_to(self.root)} ({path.stat().st_size}b)")
                if len(entries) >= max_entries:
                    return "\n".join([*entries, "... [more]"])
        return "\n".join(entries) if entries else "(empty)"
