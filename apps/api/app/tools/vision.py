"""Let the agent see images: a see_image tool backed by a vision model.

Available when a vision-capable provider (Gemini) is configured. The agent passes
a path to an uploaded image and gets a description back as untrusted [DATA] — so
a task like "here's a screenshot, what's the error?" works. Read-only.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from app.core.config import settings
from app.core.llm.providers import call_gemini_vision
from app.core.logging import get_logger
from app.tools.workspace import Workspace

log = get_logger("vision")

_MAX_BYTES = 8_000_000
_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def _mime_for(path: str) -> str | None:
    return _MIME.get(path.rsplit(".", 1)[-1].lower()) if "." in path else None


class VisionTools:
    tool_names: ClassVar[set[str]] = {"see_image"}

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if name != "see_image":
            return f"Unknown vision tool {name!r}."
        path = str(args.get("path", "")).strip()
        if not path:
            return "see_image needs a 'path' to an image file in the workspace."
        mime = _mime_for(path)
        if mime is None:
            return "Unsupported image type (use png/jpg/gif/webp)."
        try:
            target = self.workspace.resolve(path)
        except Exception as exc:
            return f"Cannot access {path}: {exc}"
        if not target.is_file():
            return f"No such file: {path}"
        data = target.read_bytes()
        if len(data) > _MAX_BYTES:
            return "Image is too large (over 8MB)."
        prompt = str(args.get("prompt", "")).strip() or "Describe this image in detail."
        async with httpx.AsyncClient(timeout=60) as client:
            desc = await call_gemini_vision(
                client, settings.gemini_api_key or "", prompt, data, mime
            )
        log.info("vision.described", path=path, chars=len(desc))
        return desc[:5000]
