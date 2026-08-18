from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..services.voice_runtime import (
    MAX_AUDIO_BYTES,
    TtsEngine,
    VoiceRuntimeError,
    voice_runtime,
)

router = APIRouter()


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    engine: TtsEngine = Field(default="auto")


def _raise_voice_error(exc: VoiceRuntimeError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from exc


@router.get("/status")
async def voice_status():
    return await voice_runtime.status()


@router.post("/tts/cosyvoice/start")
async def start_cosyvoice():
    try:
        return await voice_runtime.start_cosyvoice()
    except VoiceRuntimeError as exc:
        _raise_voice_error(exc)


@router.post("/stt")
async def voice_stt(
    file: Annotated[UploadFile, File()],
    language: str = Query(default="zh"),
):
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="录音超过 10 MB 限制")
    try:
        return await voice_runtime.transcribe(
            bytes(data),
            language=language,
            filename=file.filename or "utterance.wav",
        )
    except VoiceRuntimeError as exc:
        _raise_voice_error(exc)


@router.post("/tts")
async def voice_tts(payload: SpeakRequest):
    try:
        wav = await voice_runtime.synthesize(payload.text, engine=payload.engine)
    except VoiceRuntimeError as exc:
        _raise_voice_error(exc)
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )
