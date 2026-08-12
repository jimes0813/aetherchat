import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status

from ..services.knowledge_service import MAX_UPLOAD_BYTES, KnowledgeError, knowledge_service

router = APIRouter()


@router.post("/{session_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(session_id: UUID, file: Annotated[UploadFile, File()]):
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件超过 25 MB 限制")
    try:
        return await asyncio.to_thread(
            knowledge_service.create_attachment,
            str(session_id),
            file.filename,
            file.content_type,
            bytes(data),
        )
    except KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{session_id}/attachments")
async def list_attachments(session_id: UUID):
    return await asyncio.to_thread(knowledge_service.list_attachments, str(session_id))


@router.delete("/{session_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(session_id: UUID, attachment_id: UUID):
    try:
        await asyncio.to_thread(knowledge_service.delete_attachment, str(session_id), str(attachment_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.get("/{session_id}/attachments/{attachment_id}/preview")
async def preview_attachment(session_id: UUID, attachment_id: UUID, limit: int = Query(default=20, ge=1, le=50)):
    try:
        return await asyncio.to_thread(
            knowledge_service.preview_attachment, str(session_id), str(attachment_id), limit
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@router.get("/{session_id}/attachments/{attachment_id}/content")
async def preview_image_content(session_id: UUID, attachment_id: UUID):
    try:
        data, media_type = await asyncio.to_thread(
            knowledge_service.image_content, str(session_id), str(attachment_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except KnowledgeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.get("/{session_id}/search")
async def search_knowledge(
    session_id: UUID,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=8, ge=1, le=30),
    extension: str | None = Query(default=None, max_length=16, description="可选扩展名过滤，如 .md"),
):
    rows = await asyncio.to_thread(knowledge_service.search, str(session_id), q, limit)
    if extension:
        ext = extension if extension.startswith(".") else f".{extension}"
        ext = ext.lower()
        # Filter via attachment list metadata
        attachments = await asyncio.to_thread(knowledge_service.list_attachments, str(session_id))
        allowed = {
            item["id"]
            for item in attachments
            if str(item.get("extension") or "").lower() == ext
        }
        rows = [item for item in rows if item.get("attachment_id") in allowed]
    return rows


@router.get("/{session_id}/stats")
async def knowledge_stats(session_id: UUID):
    return await asyncio.to_thread(knowledge_service.session_stats, str(session_id))
