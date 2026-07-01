"""see_image: reads an image from the workspace and returns a description (the
vision model call is mocked; the live path needs a Gemini key)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.tools.vision import VisionTools
from app.tools.workspace import Workspace


async def test_see_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = Workspace(tmp_path / "w")
    ws.root.mkdir(parents=True, exist_ok=True)
    (ws.root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    async def fake_vision(
        client: object, api_key: str, prompt: str, image: bytes, mime: str
    ) -> str:
        assert mime == "image/png" and image.startswith(b"\x89PNG")
        return "A red square on white."

    monkeypatch.setattr("app.tools.vision.call_gemini_vision", fake_vision)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    out = await VisionTools(ws).call("see_image", {"path": "pic.png"})
    assert "red square" in out


async def test_see_image_errors(tmp_path: Path) -> None:
    vt = VisionTools(Workspace(tmp_path / "w"))
    assert "needs a 'path'" in await vt.call("see_image", {})
    assert "Unsupported" in await vt.call("see_image", {"path": "notes.txt"})
    assert "No such file" in await vt.call("see_image", {"path": "missing.png"})
