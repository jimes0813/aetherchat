from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.artifact_service import ArtifactPathError, ArtifactService  # noqa: E402


def test_missing_text_artifact_remains_visible_and_restorable(tmp_path: Path) -> None:
    service = ArtifactService(tmp_path)
    session_id = "recovery-session"
    written = asyncio.run(service.write(
        session_id,
        "notes/recover.md",
        "# Recover me\n",
        "initial snapshot",
        "tester",
    ))
    location = asyncio.run(service.native_location(session_id, written["path"]))
    Path(location["path"]).unlink()

    listed = asyncio.run(service.list_files(session_id))
    missing = next(item for item in listed if item["path"] == "notes/recover.md")
    assert missing["availability"] == "history_only"
    assert missing["recoverable"] is True

    preview = asyncio.run(service.preview(session_id, "notes/recover.md"))
    assert preview["availability"] == "history_only"
    assert preview["recoverable_version"] == 1
    assert preview["mode"] == "markdown"
    assert preview["content"] == "# Recover me\n"

    restored = asyncio.run(service.restore(session_id, "notes/recover.md", 1))
    assert restored["parent_version"] == 1
    assert asyncio.run(service.read(session_id, "notes/recover.md")) == "# Recover me\n"
    current = next(
        item
        for item in asyncio.run(service.list_files(session_id))
        if item["path"] == "notes/recover.md"
    )
    assert current["availability"] == "current"


def test_missing_binary_artifact_is_history_only_but_not_falsely_restorable(
    tmp_path: Path,
) -> None:
    service = ArtifactService(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"\xff\x00binary")
    imported = asyncio.run(service.import_file(
        "binary-session",
        "generated/source.bin",
        source,
        "binary snapshot",
        "tester",
    ))
    location = asyncio.run(
        service.native_location("binary-session", imported["path"])
    )
    Path(location["path"]).unlink()

    listed = asyncio.run(service.list_files("binary-session"))
    missing = next(item for item in listed if item["path"] == imported["path"])
    assert missing["availability"] == "history_only"
    assert missing["recoverable"] is False

    preview = asyncio.run(service.preview("binary-session", imported["path"]))
    assert preview["mode"] == "download_only"
    assert preview["recoverable_version"] is None
    with pytest.raises(ArtifactPathError, match="Binary artifact versions"):
        asyncio.run(service.restore("binary-session", imported["path"], 1))
