import errno
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.models.schemas import Message  # noqa: E402
from app.services.chat_database import ChatDatabase  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402


def test_long_conversation_and_concurrent_writes_remain_recoverable(tmp_path):
    database_path = tmp_path / "chat.sqlite3"
    database = ChatDatabase(database_path)
    session_id = uuid4()
    messages = [
        Message(
            id=uuid4(), session_id=session_id, author_model="user" if index % 2 == 0 else "offline-model",
            role="user" if index % 2 == 0 else "assistant",
            content=f"turn-{index} " + ("context " * 500), created_at=datetime.now(UTC),
        )
        for index in range(300)
    ]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(database.save_message, messages))
    assert time.monotonic() - started < 20

    reopened = ChatDatabase(database_path)
    recovered = reopened.list_messages(str(session_id), limit=1000)
    assert len(recovered) == 300
    assert {item["id"] for item in recovered} == {str(item.id) for item in messages}


def test_large_offline_knowledge_collection_stays_searchable(tmp_path):
    service = KnowledgeService(tmp_path / "knowledge")
    session_id = str(uuid4())
    for index in range(30):
        service.create_attachment(
            session_id,
            f"document-{index}.txt",
            "text/plain",
            (f"collection-marker-{index} architecture decision " + ("offline context " * 180)).encode(),
        )
    assert len(service.list_attachments(session_id)) == 30
    hits = service.search(session_id, "collection-marker-23 architecture decision", limit=8)
    assert hits
    assert hits[0]["source_name"] == "document-23.txt"


def test_low_disk_write_fails_cleanly_without_indexing_partial_attachment(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path / "knowledge")
    session_id = str(uuid4())
    original = Path.write_bytes

    def fail_source_write(path: Path, data: bytes):
        if path.name == "source.txt":
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_source_write)
    with pytest.raises(OSError) as rejected:
        service.create_attachment(session_id, "source.txt", "text/plain", b"must not be indexed")
    assert rejected.value.errno == errno.ENOSPC
    assert service.list_attachments(session_id) == []
    session_root = service.base_path / session_id
    assert not session_root.exists() or list(session_root.iterdir()) == []
