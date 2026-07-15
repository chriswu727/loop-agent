"""Transactional Git change sets for local project-backed tasks."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.exceptions import ConflictError, ValidationError

_RECEIPT_PATHS = ("receipt.json", "RECEIPT.md")
_STALE_LOCK_SECONDS = 600


def _git_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **(extra or {}),
    }


@dataclass(frozen=True)
class ProjectBinding:
    source: Path
    relative_path: str
    base_commit: str
    branch: str | None


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    additions: int | None
    deletions: int | None
    previous_path: str | None = None


@dataclass(frozen=True)
class ChangeSetSnapshot:
    patch: bytes
    patch_sha256: str
    files: list[FileChange]
    diff: str
    diff_truncated: bool


class GitCommandError(RuntimeError):
    pass


def _git(
    repo: Path,
    *args: str,
    input_data: bytes | None = None,
    env: dict[str, str] | None = None,
) -> bytes:
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            *args,
        ],
        input=input_data,
        capture_output=True,
        check=False,
        timeout=120,
        env=_git_environment(env),
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitCommandError(detail or f"git {' '.join(args)} failed")
    return completed.stdout


def _projects_root() -> Path:
    configured = settings.loop_local_projects_root
    if not configured:
        raise ValidationError(
            "Local project tasks are disabled; set LOOP_LOCAL_PROJECTS_ROOT first."
        )
    try:
        root = Path(configured).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValidationError("LOOP_LOCAL_PROJECTS_ROOT does not exist.") from exc
    if not root.is_dir():
        raise ValidationError("LOOP_LOCAL_PROJECTS_ROOT must be a directory.")
    return root


def _allowed_source(source: Path) -> tuple[Path, Path]:
    root = _projects_root()
    try:
        resolved = source.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValidationError("The requested local project does not exist.") from exc
    if resolved != root and root not in resolved.parents:
        raise ValidationError("The requested project escapes LOOP_LOCAL_PROJECTS_ROOT.")
    return root, resolved


def prepare_project(relative_path: str) -> ProjectBinding:
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValidationError("project_path must be relative to LOOP_LOCAL_PROJECTS_ROOT.")
    root, source = _allowed_source(_projects_root() / requested)
    try:
        top = Path(_git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    except (GitCommandError, OSError) as exc:
        raise ValidationError("The requested project is not a Git repository.") from exc
    if top != source:
        raise ValidationError("project_path must identify the Git repository root.")
    dirty = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise ConflictError(
            "The source project has uncommitted changes. Commit or stash them before starting "
            "an isolated task."
        )
    base_commit = _git(source, "rev-parse", "HEAD").decode().strip()
    branch_raw = _git(source, "branch", "--show-current").decode().strip()
    branch = branch_raw or None
    return ProjectBinding(
        source=source,
        relative_path=source.relative_to(root).as_posix() or ".",
        base_commit=base_commit,
        branch=branch,
    )


def clone_project(binding: ProjectBinding, destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        if any(destination.iterdir()):
            raise ConflictError("The isolated project workspace already exists and is not empty.")
        destination.rmdir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "clone",
                "--quiet",
                "--no-local",
                "--no-checkout",
                str(binding.source),
                str(destination),
            ],
            capture_output=True,
            check=False,
            timeout=120,
            env=_git_environment(),
        )
        if completed.returncode != 0:
            raise GitCommandError(completed.stderr.decode("utf-8", errors="replace").strip())
        _git(destination, "checkout", "--quiet", "--detach", binding.base_commit)
        _git(destination, "remote", "remove", "origin")
    except (GitCommandError, OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ConflictError(f"Could not create the isolated Git checkout: {exc}") from exc


def _temporary_index(
    repo: Path, base_commit: str
) -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    temporary = tempfile.TemporaryDirectory(prefix="loop-git-index-")
    index = Path(temporary.name) / "index"
    env = {"GIT_INDEX_FILE": str(index)}
    _git(repo, "read-tree", base_commit, env=env)
    _git(
        repo,
        "add",
        "-A",
        "--",
        ".",
        *[f":(exclude){path}" for path in _RECEIPT_PATHS],
        env=env,
    )
    return env, temporary


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _parse_name_status(raw: bytes) -> list[tuple[str, str, str | None]]:
    tokens = [token for token in raw.split(b"\0") if token]
    parsed: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", errors="replace")
        index += 1
        first = _decode_path(tokens[index])
        index += 1
        previous: str | None = None
        path = first
        if status[:1] in {"R", "C"}:
            previous = first
            path = _decode_path(tokens[index])
            index += 1
        parsed.append((status, path, previous))
    return parsed


def _parse_numstat(raw: bytes) -> dict[str, tuple[int | None, int | None]]:
    tokens = raw.split(b"\0")
    stats: dict[str, tuple[int | None, int | None]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        parts = token.split(b"\t", 2)
        if len(parts) != 3:
            continue
        additions_raw, deletions_raw, path_raw = parts
        if path_raw:
            path = _decode_path(path_raw)
        else:
            if index + 1 >= len(tokens):
                break
            index += 1
            path = _decode_path(tokens[index])
            index += 1
        additions = None if additions_raw == b"-" else int(additions_raw)
        deletions = None if deletions_raw == b"-" else int(deletions_raw)
        stats[path] = (additions, deletions)
    return stats


def inspect_changes(repo: Path, base_commit: str) -> ChangeSetSnapshot:
    try:
        env, temporary = _temporary_index(repo, base_commit)
        try:
            patch = _git(
                repo,
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--find-renames",
                base_commit,
                env=env,
            )
            raw_status = _git(
                repo,
                "diff",
                "--cached",
                "--name-status",
                "-z",
                "--find-renames",
                base_commit,
                env=env,
            )
            raw_stats = _git(
                repo,
                "diff",
                "--cached",
                "--numstat",
                "-z",
                "--find-renames",
                base_commit,
                env=env,
            )
            preview_raw = _git(
                repo,
                "diff",
                "--cached",
                "--no-color",
                "--no-ext-diff",
                "--find-renames",
                base_commit,
                env=env,
            )
        finally:
            temporary.cleanup()
    except GitCommandError as exc:
        raise ConflictError(f"Could not inspect the isolated Git checkout: {exc}") from exc

    stats = _parse_numstat(raw_stats)
    files = [
        FileChange(
            path=path,
            status=status,
            additions=stats.get(path, (None, None))[0],
            deletions=stats.get(path, (None, None))[1],
            previous_path=previous,
        )
        for status, path, previous in _parse_name_status(raw_status)
    ]
    preview_limit = settings.loop_changeset_preview_bytes
    diff_truncated = len(preview_raw) > preview_limit
    preview = preview_raw[:preview_limit].decode("utf-8", errors="replace")
    if diff_truncated:
        preview += "\n... [diff preview truncated]"
    return ChangeSetSnapshot(
        patch=patch,
        patch_sha256=hashlib.sha256(patch).hexdigest(),
        files=files,
        diff=preview,
        diff_truncated=diff_truncated,
    )


def _verify_source(source_path: str, base_commit: str, *, require_clean: bool) -> Path:
    _, source = _allowed_source(Path(source_path))
    try:
        current = _git(source, "rev-parse", "HEAD").decode().strip()
        if current != base_commit:
            raise ConflictError(
                "The source project moved to a different commit; this change set cannot be applied "
                "safely."
            )
        if require_clean and _git(source, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ConflictError(
                "The source project has uncommitted changes; Apply refuses to overwrite them."
            )
    except GitCommandError as exc:
        raise ConflictError(f"Could not verify the source project: {exc}") from exc
    return source


def apply_patch(
    source_path: str,
    base_commit: str,
    patch: bytes,
    *,
    reverse: bool = False,
    allow_dirty: bool = False,
) -> None:
    source = _verify_source(source_path, base_commit, require_clean=not reverse and not allow_dirty)
    args = ["apply", "--binary"]
    if reverse:
        args.append("--reverse")
    try:
        _git(source, *args, "--check", input_data=patch)
        _git(source, *args, input_data=patch)
    except GitCommandError as exc:
        action = "Undo" if reverse else "Apply"
        raise ConflictError(f"{action} could not be completed without conflicts: {exc}") from exc


def _metadata_root() -> Path:
    root = Path(settings.agent_workspaces_root).expanduser().resolve() / ".changesets"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def acquire_source_lock(source_path: str) -> Path:
    digest = hashlib.sha256(str(Path(source_path).resolve()).encode()).hexdigest()
    lock = _metadata_root() / f"source-{digest}.lock"
    for attempt in range(2):
        try:
            lock.mkdir()
            return lock
        except FileExistsError as exc:
            try:
                stale = time.time() - lock.stat().st_mtime > _STALE_LOCK_SECONDS
            except OSError:
                stale = False
            if attempt == 0 and stale:
                shutil.rmtree(lock, ignore_errors=True)
                continue
            raise ConflictError(
                "Another change-set operation is in progress for this project."
            ) from exc
    raise ConflictError("Could not acquire the project change-set lock.")


def release_source_lock(lock: Path) -> None:
    shutil.rmtree(lock, ignore_errors=True)


def patch_path(task_id: uuid.UUID) -> Path:
    return _metadata_root() / f"{task_id}.patch"


def save_patch(task_id: uuid.UUID, patch: bytes) -> None:
    target = patch_path(task_id)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(patch)
    temporary.chmod(0o600)
    temporary.replace(target)


def load_patch(task_id: uuid.UUID, expected_sha256: str) -> bytes:
    target = patch_path(task_id)
    try:
        patch = target.read_bytes()
    except OSError as exc:
        raise ConflictError("The applied patch record is missing; Undo was refused.") from exc
    if hashlib.sha256(patch).hexdigest() != expected_sha256:
        raise ConflictError(
            "The applied patch record failed its integrity check; Undo was refused."
        )
    return patch


def delete_patch(task_id: uuid.UUID) -> None:
    patch_path(task_id).unlink(missing_ok=True)
