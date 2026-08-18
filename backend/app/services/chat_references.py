"""Resolve explicit citations of the current conversation.

Users can cite messages, images, and documents that already exist in this
chat. The model only sees those citations if we expand them into labeled
evidence and state that they are part of the user's intent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_service import ArtifactPathError, ArtifactService
from .chat_database import chat_database
from .knowledge_service import IMAGE_EXTENSIONS, KnowledgeError, knowledge_service

MAX_REFERENCES = 8
MAX_MESSAGE_CHARS = 2_500
MAX_CONTEXT_CHARS = 16_000
ALLOWED_KINDS = {"attachment", "file", "image", "generated", "message"}
_artifact_service = ArtifactService()


def normalize_references(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    resolved: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw[:MAX_REFERENCES]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind in {"file", "document"}:
            kind = "attachment"
        if kind == "conversation":
            continue
        if kind not in ALLOWED_KINDS:
            continue
        ident = str(item.get("id") or "").strip()
        if not ident:
            continue
        key = (kind, ident)
        if key in seen:
            continue
        seen.add(key)
        payload = {"kind": kind, "id": ident}
        session_id = str(item.get("session_id") or "").strip()
        if session_id:
            payload["session_id"] = session_id
        resolved.append(payload)
    return resolved


def _clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _message_line(row: dict[str, Any]) -> str:
    role = "用户" if row.get("role") == "user" else ("助手" if row.get("role") == "assistant" else str(row.get("role") or "消息"))
    author = str(row.get("author_model") or "").strip()
    who = f"{role} {author}".strip() if author and role != "用户" else role
    return f"{who}：{_clip(str(row.get('content') or ''), MAX_MESSAGE_CHARS)}"


def _attachment_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "未命名文件")
    extension = str(item.get("extension") or "").lower()
    if extension in IMAGE_EXTENSIONS:
        return f"图片「{name}」"
    return f"文档「{name}」"


def _artifact_file(session_id: str, path: str) -> Path | None:
    try:
        full, _, _ = _artifact_service._artifact_path(session_id, path)
    except (ArtifactPathError, ValueError):
        return None
    if not full.is_file() or full.is_symlink():
        return None
    return full


def _find_generated_file(session_id: str, ident: str) -> dict[str, Any] | None:
    for row in chat_database.list_messages(session_id, 200):
        if str(row.get("session_id") or "") not in {"", session_id}:
            continue
        for item in row.get("generated_files") or []:
            if not isinstance(item, dict):
                continue
            if ident in {
                str(item.get("path") or ""),
                str(item.get("url") or ""),
                str(item.get("name") or ""),
            }:
                return item
        for item in row.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            if ident in {str(item.get("id") or ""), str(item.get("name") or "")}:
                return {**item, "path": item.get("path"), "from_attachment": True}
    return None


def _generated_block(session_id: str, item: dict[str, Any]) -> tuple[str, str | None]:
    name = str(item.get("name") or item.get("path") or "未命名文件")
    path = str(item.get("path") or "")
    mime = str(item.get("mime_type") or "")
    suffix = Path(name).suffix.lower() or Path(path).suffix.lower()
    image = mime.startswith("image/") or suffix in IMAGE_EXTENSIONS
    label = f"图片「{name}」" if image else f"文档「{name}」"
    local = _artifact_file(session_id, path) if path else None
    if image:
        note = (
            f"[引用 · {label}]\n"
            "这是当前对话中已经出现的图片。请把它当作用户正在指着的那张图来理解意图并继续。"
        )
        return note, (str(local) if local else None)
    if local and local.stat().st_size <= 200_000:
        text = local.read_text(encoding="utf-8", errors="replace")[:3_000].strip()
        if text:
            return f"[引用 · {label}]\n{text}", None
    return f"[引用 · {label}]\n这是当前对话中已经出现的文档，请按用户这句话使用它。", None


def resolve_chat_references(
    *,
    session_id: str,
    user_content: str,
    references: Any,
    attachment_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Expand citations into labeled context the model must treat as intent."""

    refs = normalize_references(references)
    merged_ids = [str(value) for value in (attachment_ids or []) if str(value).strip()]
    labels: list[str] = []
    blocks: list[str] = []
    image_paths: list[str] = []

    def add_attachment(ident: str) -> None:
        try:
            item = knowledge_service.get_attachment(session_id, ident)
        except (KeyError, KnowledgeError, ValueError):
            generated = _find_generated_file(session_id, ident)
            if generated is None:
                return
            label = str(generated.get("name") or ident)
            block, extra_image = _generated_block(session_id, generated)
            labels.append(label)
            blocks.append(block)
            if extra_image:
                image_paths.append(extra_image)
            return
        if item["id"] not in merged_ids:
            merged_ids.append(item["id"])
        label = _attachment_label(item)
        labels.append(label)
        if str(item.get("extension") or "").lower() in IMAGE_EXTENSIONS:
            blocks.append(
                f"[引用 · {label}]\n"
                "这是当前对话中的图片，已作为视觉输入提供。请直接看图并按用户这句话使用它。"
            )
            return
        excerpt = knowledge_service.explicit_attachment_context(
            session_id,
            [item["id"]],
            max_chars=4_000,
        )
        blocks.append(f"[引用 · {label}]\n{excerpt or '（该文档没有可提取的文本，但用户已明确引用它。）'}")

    for ref in refs:
        kind = ref["kind"]
        ident = ref["id"]
        if kind in {"attachment", "image", "generated"}:
            add_attachment(ident)
            continue

        if kind == "message":
            row = chat_database.get_message(ident)
            if row is None or str(row.get("session_id") or "") != str(session_id):
                continue
            preview = _clip(str(row.get("content") or ""), 80)
            labels.append(f"本对话消息「{preview or ident}」")
            blocks.append(f"[引用 · 当前对话中的这条消息]\n{_message_line(row)}")
            for item in row.get("attachments") or []:
                if isinstance(item, dict) and item.get("id"):
                    add_attachment(str(item["id"]))
            for item in row.get("generated_files") or []:
                if not isinstance(item, dict):
                    continue
                block, extra_image = _generated_block(session_id, item)
                labels.append(str(item.get("name") or "生成文件"))
                blocks.append(block)
                if extra_image:
                    image_paths.append(extra_image)

    unique_ids: list[str] = []
    seen_ids: set[str] = set()
    for value in merged_ids:
        if value in seen_ids:
            continue
        seen_ids.add(value)
        unique_ids.append(value)

    if not labels and not unique_ids:
        return {"context": "", "attachment_ids": unique_ids, "labels": [], "image_paths": image_paths}

    cited = "、".join(labels) if labels else "附带资料"
    heading = (
        "用户在本条消息中明确引用了当前对话里的下列内容。这些引用就是用户意图的一部分："
        "必须直接使用它们完成请求，不要假装没看到，也不要让用户再次粘贴。"
        f"\n用户原文：{user_content or '（无文字，仅引用）'}"
        f"\n已引用：{cited}"
    )
    parts = [heading]
    used = len(heading)
    for block in blocks:
        section = f"\n\n{block}"
        if used + len(section) > MAX_CONTEXT_CHARS:
            break
        parts.append(section)
        used += len(section)
    return {
        "context": "".join(parts),
        "attachment_ids": unique_ids,
        "labels": labels,
        "image_paths": image_paths,
    }
