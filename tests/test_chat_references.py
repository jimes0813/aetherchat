from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import Message  # noqa: E402
from app.services.chat_database import chat_database  # noqa: E402
from app.services.chat_references import (  # noqa: E402
    normalize_references,
    resolve_chat_references,
)


def test_normalize_references_dedupes_and_aliases_file_kind():
    refs = normalize_references([
        {"kind": "file", "id": "a1"},
        {"kind": "attachment", "id": "a1"},
        {"kind": "conversation", "id": "s1"},
        {"kind": "nope", "id": "x"},
        {"kind": "message"},
    ])
    assert refs == [
        {"kind": "attachment", "id": "a1"},
    ]


def test_resolve_current_conversation_message_states_user_intent():
    current_id = uuid4()
    other_id = uuid4()
    chat_database.create_session(str(current_id), "当前对话", "casual")
    chat_database.create_session(str(other_id), "别的对话", "casual")
    now = datetime.now(UTC)

    first = Message(
        id=uuid4(),
        session_id=current_id,
        author_model="user",
        role="user",
        content="封面要用雨夜街头",
        created_at=now,
    )
    outsider = Message(
        id=uuid4(),
        session_id=other_id,
        author_model="user",
        role="user",
        content="别的会话里的话",
        created_at=now,
    )
    chat_database.save_message(first)
    chat_database.save_message(outsider)

    resolved = resolve_chat_references(
        session_id=str(current_id),
        user_content="按刚才那条继续",
        references=[
            {"kind": "conversation", "id": str(other_id)},
            {"kind": "message", "id": str(outsider.id)},
            {"kind": "message", "id": str(first.id)},
        ],
    )
    assert "这些引用就是用户意图的一部分" in resolved["context"]
    assert "当前对话里" in resolved["context"]
    assert "按刚才那条继续" in resolved["context"]
    assert "封面要用雨夜街头" in resolved["context"]
    assert "别的会话里的话" not in resolved["context"]
    assert "对话「别的对话」" not in resolved["context"]
    assert resolved["attachment_ids"] == []
