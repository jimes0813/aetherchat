"""F0: SQLite-backed approvals survive process boundaries; restart cancels pending."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-approvals-"))
os.environ.setdefault("AETHERCHAT_ALLOW_UNAUTHENTICATED", "1")


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    # Rebuild chat_database against the temp dir.
    from app.services import chat_database as chat_db_module
    from app.services.chat_database import ChatDatabase
    from app.services import approval_service as approval_module

    db = ChatDatabase(path=tmp_path / "aetherchat.sqlite3")
    monkeypatch.setattr(chat_db_module, "chat_database", db)
    service = approval_module.ApprovalService()
    monkeypatch.setattr(approval_module, "approval_service", service)
    # Point service._db at the new singleton via chat_database module.
    return service, db


def test_create_persists_and_decide_resolves(isolated_db):
    service, db = isolated_db

    async def _run():
        pending = service.create(
            session_id="sess-1",
            model="test",
            tool_name="run_command",
            risk="command_execution",
            title="run?",
            detail="echo hi",
            arguments={"cmd": "echo hi"},
            allow_session=True,
        )
        assert pending is not None
        rows = db.list_pending_approvals()
        assert any(item["id"] == pending.id for item in rows)

        async def decide_later():
            await asyncio.sleep(0.01)
            return service.decide(pending.id, "sess-1", "allow_once")

        decision_task = asyncio.create_task(decide_later())
        result = await service.wait(pending.id, timeout=2.0)
        decided = await decision_task
        assert result == "allow_once"
        assert decided["resolved"] is True

    asyncio.run(_run())


def test_bootstrap_cancels_orphaned_pending(isolated_db):
    service, db = isolated_db
    db.upsert_pending_approval(
        {
            "id": "orphan-1",
            "session_id": "sess-2",
            "model": "test",
            "tool_name": "run_command",
            "risk": "command_execution",
            "title": "orphan",
            "detail": "left over",
            "arguments": {},
            "allow_session": True,
            "created_at": "2026-10-14T00:00:00+00:00",
            "status": "pending",
        }
    )
    summary = service.bootstrap_from_db()
    assert summary["cancelled_pending"] >= 1
    cancelled = service.list_restart_cancellations()
    assert any(item["id"] == "orphan-1" for item in cancelled)
    assert all(item.get("cancel_reason") == "backend_restart" for item in cancelled if item["id"] == "orphan-1")
    assert db.list_pending_approvals() == []


def test_session_grant_persists(isolated_db):
    service, db = isolated_db
    db.upsert_session_grant("sess-3", "command_execution", ttl_seconds=3600)
    assert service.is_session_granted("sess-3", "command_execution") is True
    assert service.is_session_granted("sess-3", "other") is False
