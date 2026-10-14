"""Schema migration + reindex compatibility for knowledge vectors."""

from __future__ import annotations

import os
import sqlite3
import sys
from array import array
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.embedding_client import LOCAL_HASH_DIMENSIONS, LOCAL_HASH_PROVIDER  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402


def test_migrates_legacy_vectors_and_reindex(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_PROVIDER", "local-hash")
    for key in (
        "AETHERCHAT_EMBEDDING_API_KEY",
        "AETHERCHAT_EMBEDDING_BASE_URL",
        "AETHERCHAT_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    data = tmp_path / "data"
    data.mkdir()
    db = data / "knowledge.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE knowledge_attachments (
            id TEXT PRIMARY KEY, session_id TEXT, name TEXT, extension TEXT,
            content_type TEXT, size_bytes INTEGER, sha256 TEXT, status TEXT,
            chunk_count INTEGER, created_at TEXT
        );
        CREATE VIRTUAL TABLE knowledge_chunks USING fts5(
            id UNINDEXED, session_id UNINDEXED, attachment_id UNINDEXED,
            source_name, locator, content, tokenize='unicode61'
        );
        CREATE TABLE knowledge_vectors (
            id TEXT PRIMARY KEY, session_id TEXT, attachment_id TEXT, vector BLOB NOT NULL
        );
        """
    )
    sid = str(uuid4())
    aid = str(uuid4())
    cid = str(uuid4())
    blob = array("f", [0.1] * 256).tobytes()
    conn.execute(
        "INSERT INTO knowledge_attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, sid, "a.md", ".md", "text/markdown", 10, "abc", "ready", 1, "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO knowledge_chunks VALUES (?,?,?,?,?,?)",
        (cid, sid, aid, "a.md", "全文", "legacy chunk about embeddings"),
    )
    conn.execute(
        "INSERT INTO knowledge_vectors VALUES (?,?,?,?)",
        (cid, sid, aid, blob),
    )
    conn.commit()
    conn.close()

    service = KnowledgeService(base_path=data)
    status = service.embedding_status()
    assert status["vector_count"] >= 1
    stored = status["stored"]
    assert any(item.get("provider") == LOCAL_HASH_PROVIDER for item in stored)

    result = service.reindex_vectors(sid, force_local=True)
    assert result["vector_count"] >= 1
    assert result["dimensions"] == LOCAL_HASH_DIMENSIONS
    assert result["awaiting_network"] is False

    stats = service.session_stats(sid)
    assert stats["retrieval"]["offline"] is True
    assert "local-hash" in stats["retrieval"]["vector"]


def test_create_attachment_stores_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_EMBEDDING_PROVIDER", "local-hash")
    service = KnowledgeService(base_path=tmp_path / "data2")
    sid = str(uuid4())
    service.create_attachment(sid, "note.txt", "text/plain", b"network embeddings hybrid retrieval")
    status = service.embedding_status()
    assert status["vector_count"] >= 1
    assert all(item["dimensions"] == LOCAL_HASH_DIMENSIONS for item in status["stored"])
