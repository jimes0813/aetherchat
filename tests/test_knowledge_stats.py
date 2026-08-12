"""Knowledge session stats and layered search fields (service-level)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-knowledge-"))

from app.services.knowledge_service import KnowledgeService  # noqa: E402


def test_search_includes_layer_scores_and_stats(tmp_path):
    service = KnowledgeService(base_path=tmp_path / "data")
    session_id = str(uuid4())
    payload = b"# hybrid retrieval\noffline fulltext search and hash similarity\n"
    service.create_attachment(
        session_id,
        "requirements.md",
        "text/markdown",
        payload,
    )

    stats = service.session_stats(session_id)
    assert stats["attachment_count"] == 1
    assert stats["chunk_count"] >= 1
    assert stats["vector_count"] >= 1
    assert stats["retrieval"]["offline"] is True
    assert stats["ready_count"] == 1

    rows = service.search(session_id, "offline fulltext search", limit=8)
    assert rows
    assert "layers" in rows[0]
    assert "lexical" in rows[0]["layers"]
    assert rows[0]["rank"] == 1
    assert rows[0]["score"] > 0
