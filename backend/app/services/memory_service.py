from __future__ import annotations

import re
from typing import Any

from .chat_database import ChatDatabase, chat_database

DECISION_RE = re.compile(r"(?:决定|确定|采用|选择|结论|统一使用|改为|定为)")
PREFERENCE_RE = re.compile(r"(?:我希望|我偏好|偏好|请始终|以后请|默认使用|不要|必须使用|优先使用)")
OPEN_ITEM_RE = re.compile(r"(?:待办|TODO|下一步|还需要|需要继续|尚未|未完成|后续需要)", re.IGNORECASE)


def _compact(value: str, limit: int = 320) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _sentences(content: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", str(content or ""))
    return [item for item in (_compact(part) for part in parts) if len(item) >= 3]


def _append_unique(items: list[str], value: str, limit: int = 30) -> list[str]:
    normalized = value.casefold()
    result = [item for item in items if item.casefold() != normalized]
    result.append(value)
    return result[-limit:]


class MemoryService:
    def __init__(self, database: ChatDatabase | None = None):
        self.database = database or chat_database

    def refresh(self, session_id: str, *, user_content: str | None = None) -> dict[str, Any]:
        memory = self.database.get_memory(session_id)
        decisions = list(memory.get("decisions") or [])
        preferences = list(memory.get("preferences") or [])
        open_items = list(memory.get("open_items") or [])
        if user_content:
            for sentence in _sentences(user_content):
                if DECISION_RE.search(sentence):
                    decisions = _append_unique(decisions, sentence)
                if PREFERENCE_RE.search(sentence):
                    preferences = _append_unique(preferences, sentence)
                if OPEN_ITEM_RE.search(sentence):
                    open_items = _append_unique(open_items, sentence)

        messages = self.database.list_messages(session_id, limit=16)
        lines = []
        for message in messages:
            if message.get("role") not in {"user", "assistant"}:
                continue
            speaker = "用户" if message.get("role") == "user" else str(message.get("author_model") or "助手")
            text = _compact(message.get("content", ""), 220)
            if text:
                lines.append(f"- {speaker}: {text}")
        summary = "最近对话摘要：\n" + "\n".join(lines[-12:]) if lines else ""
        return self.database.save_memory(
            session_id,
            summary=summary,
            project_note=str(memory.get("project_note") or ""),
            decisions=decisions,
            preferences=preferences,
            open_items=open_items,
            message_count=len(messages),
        )

    def update(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.database.get_memory(session_id)

        def string_list(field: str) -> list[str]:
            value = patch.get(field, current.get(field) or [])
            if not isinstance(value, list):
                raise ValueError(f"{field} 必须是字符串数组")
            result = []
            for item in value[:30]:
                text = _compact(item)
                if text and text not in result:
                    result.append(text)
            return result

        project_note = patch.get("project_note", current.get("project_note") or "")
        if not isinstance(project_note, str):
            raise ValueError("project_note 必须是字符串")
        return self.database.save_memory(
            session_id,
            summary=str(current.get("summary") or ""),
            project_note=_compact(project_note, 4000),
            decisions=string_list("decisions"),
            preferences=string_list("preferences"),
            open_items=string_list("open_items"),
            message_count=int(current.get("message_count") or 0),
        )

    def context(self, session_id: str, max_chars: int = 6000) -> str:
        memory = self.database.get_memory(session_id)
        tasks = [
            task for task in self.database.list_tasks_for_session(session_id)
            if task.get("status") not in {"done", "merged"}
        ]
        sections = [
            "以下是当前会话由 AetherChat 本机保存的项目记忆。它用于保持连续性；若与用户当前请求冲突，以当前请求为准。",
        ]
        if memory.get("project_note"):
            sections.append(f"项目固定说明：\n{memory['project_note']}")
        if memory.get("preferences"):
            sections.append("用户明确偏好：\n- " + "\n- ".join(memory["preferences"][-12:]))
        if memory.get("decisions"):
            sections.append("已记录决定：\n- " + "\n- ".join(memory["decisions"][-12:]))
        if memory.get("open_items"):
            sections.append("对话中的未完成事项：\n- " + "\n- ".join(memory["open_items"][-12:]))
        if tasks:
            sections.append("任务板未完成任务：\n- " + "\n- ".join(
                f"[{task['status']}] {task['title']}" for task in tasks[:20]
            ))
        if memory.get("summary"):
            sections.append(str(memory["summary"]))
        content = "\n\n".join(sections)
        return content[:max_chars] if len(sections) > 1 else ""


memory_service = MemoryService()
