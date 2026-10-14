"""OpenAI-compatible network STT/TTS with vault auto-select.

Env:
  AETHERCHAT_VOICE_STT_PROVIDER=auto|local|network
  AETHERCHAT_VOICE_TTS_PROVIDER=auto|local|network
  AETHERCHAT_VOICE_STT_MODEL / AETHERCHAT_VOICE_TTS_MODEL
  AETHERCHAT_VOICE_BASE_URL / AETHERCHAT_VOICE_API_KEY
  (or reuse AETHERCHAT_EMBEDDING_* style vault keys via models.json)

auto: prefer local engines when ready; soft-fallback to network.
network: network only. local: local only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("aetherchat.voice_network")

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_OPENAI_STT_MODEL = "whisper-1"
DEFAULT_OPENAI_TTS_MODEL = "tts-1"
DEFAULT_OPENAI_TTS_VOICE = "alloy"
DEFAULT_ZHIPU_STT_MODEL = "glm-asr-2512"
DEFAULT_ZHIPU_TTS_MODEL = "glm-tts"
DEFAULT_ZHIPU_TTS_VOICE = "tongtong"


@dataclass(frozen=True)
class VoiceNetworkConfig:
    provider: str  # openai | zhipu | compat | none
    base_url: str | None
    api_key: str | None
    stt_model: str | None
    tts_model: str | None
    tts_voice: str | None
    source: str

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.base_url and self.provider != "none")

    @property
    def label(self) -> str:
        if not self.ready:
            return "network-unavailable"
        return f"{self.provider}:{self.stt_model or '-'}/{self.tts_model or '-'}"


def _normalize_base_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _audio_path(base_url: str, kind: str) -> str:
    base = _normalize_base_url(base_url)
    if base.endswith(f"/audio/{kind}"):
        return base
    return f"{base}/audio/{kind}"


def _vault_key(ref: str) -> str | None:
    if not ref:
        return None
    env_direct = os.getenv(ref)
    if env_direct:
        return env_direct.strip() or None
    try:
        from .vault import vault

        value = vault.get_key(ref)
        return (value or "").strip() or None
    except Exception:
        return None


def _load_models_json() -> list[dict[str, Any]]:
    data_dir = os.environ.get("AETHERCHAT_DATA_DIR")
    candidates: list[Path] = []
    if data_dir:
        candidates.append(Path(data_dir).expanduser() / "models.json")
    candidates.append(Path.home() / ".config" / "aetherchat-frontend" / "backend-data" / "models.json")
    candidates.append(Path.home() / ".local" / "share" / "aetherchat" / "models.json")
    for path in candidates:
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
        except Exception:
            continue
    return []


def _host_kind(base_url: str | None) -> str:
    host = urlparse(base_url or "").netloc or (base_url or "")
    blob = f"{host} {base_url or ''}".lower()
    if re.search(r"(bigmodel\.cn|zhipu|智谱)", blob):
        return "zhipu"
    if re.search(r"(api\.openai\.com|openai\.azure\.com)", blob):
        return "openai"
    return "compat"


def resolve_voice_network_config() -> VoiceNetworkConfig:
    env_base = (os.getenv("AETHERCHAT_VOICE_BASE_URL") or "").strip() or None
    env_key = (os.getenv("AETHERCHAT_VOICE_API_KEY") or "").strip() or None
    if not env_key:
        env_key = _vault_key("AETHERCHAT_VOICE_API_KEY")
    env_stt = (os.getenv("AETHERCHAT_VOICE_STT_MODEL") or "").strip() or None
    env_tts = (os.getenv("AETHERCHAT_VOICE_TTS_MODEL") or "").strip() or None
    env_voice = (os.getenv("AETHERCHAT_VOICE_TTS_VOICE") or "").strip() or None

    if env_key and env_base:
        kind = _host_kind(env_base)
        return VoiceNetworkConfig(
            provider=kind,
            base_url=_normalize_base_url(env_base),
            api_key=env_key,
            stt_model=env_stt
            or (DEFAULT_ZHIPU_STT_MODEL if kind == "zhipu" else DEFAULT_OPENAI_STT_MODEL),
            tts_model=env_tts
            or (DEFAULT_ZHIPU_TTS_MODEL if kind == "zhipu" else DEFAULT_OPENAI_TTS_MODEL),
            tts_voice=env_voice
            or (DEFAULT_ZHIPU_TTS_VOICE if kind == "zhipu" else DEFAULT_OPENAI_TTS_VOICE),
            source="env",
        )

    if env_key and not env_base:
        return VoiceNetworkConfig(
            provider="openai",
            base_url=DEFAULT_OPENAI_BASE,
            api_key=env_key,
            stt_model=env_stt or DEFAULT_OPENAI_STT_MODEL,
            tts_model=env_tts or DEFAULT_OPENAI_TTS_MODEL,
            tts_voice=env_voice or DEFAULT_OPENAI_TTS_VOICE,
            source="env:openai-default",
        )

    models = _load_models_json()

    def pick(predicate, *, default_base: str, kind: str, source: str) -> VoiceNetworkConfig | None:
        for item in models:
            if not predicate(item):
                continue
            ref = str(item.get("api_key_ref") or "")
            key = _vault_key(ref) if ref else None
            if not key:
                continue
            base = str(item.get("base_url") or default_base).strip() or default_base
            return VoiceNetworkConfig(
                provider=kind,
                base_url=_normalize_base_url(base),
                api_key=key,
                stt_model=env_stt
                or (DEFAULT_ZHIPU_STT_MODEL if kind == "zhipu" else DEFAULT_OPENAI_STT_MODEL),
                tts_model=env_tts
                or (DEFAULT_ZHIPU_TTS_MODEL if kind == "zhipu" else DEFAULT_OPENAI_TTS_MODEL),
                tts_voice=env_voice
                or (DEFAULT_ZHIPU_TTS_VOICE if kind == "zhipu" else DEFAULT_OPENAI_TTS_VOICE),
                source=source,
            )
        return None

    chosen = pick(
        lambda m: (
            str(m.get("api_key_ref") or "").upper() in {"OPENAI_API_KEY", "MODEL_KEY_GPT"}
            or (str(m.get("name") or "").lower() in {"gpt", "openai"})
        ),
        default_base=DEFAULT_OPENAI_BASE,
        kind="openai",
        source="vault:openai",
    )
    if chosen:
        return chosen

    chosen = pick(
        lambda m: (
            "bigmodel.cn" in str(m.get("base_url") or "").lower()
            or str(m.get("name") or "").lower() in {"glm", "zhipu", "智谱"}
        ),
        default_base=DEFAULT_ZHIPU_BASE,
        kind="zhipu",
        source="vault:zhipu",
    )
    if chosen:
        return chosen

    return VoiceNetworkConfig(
        provider="none",
        base_url=None,
        api_key=None,
        stt_model=None,
        tts_model=None,
        tts_voice=None,
        source="unavailable",
    )


def voice_provider_preference(kind: str) -> str:
    """kind is 'stt' or 'tts'."""
    key = "AETHERCHAT_VOICE_STT_PROVIDER" if kind == "stt" else "AETHERCHAT_VOICE_TTS_PROVIDER"
    value = (os.getenv(key) or "auto").strip().lower()
    if value in {"local", "network", "auto"}:
        return value
    return "auto"


class VoiceNetworkError(RuntimeError):
    def __init__(self, message: str, *, code: str = "voice_network_error", status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


async def network_transcribe(
    audio: bytes,
    *,
    filename: str = "utterance.wav",
    language: str = "zh",
    config: VoiceNetworkConfig | None = None,
) -> dict[str, Any]:
    cfg = config or resolve_voice_network_config()
    if not cfg.ready:
        raise VoiceNetworkError("网络识别未配置", code="network_stt_unavailable", status_code=503)
    url = _audio_path(cfg.base_url or "", "transcriptions")
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    data: dict[str, str] = {"model": cfg.stt_model or DEFAULT_OPENAI_STT_MODEL}
    if language and language != "auto" and cfg.provider != "zhipu":
        data["language"] = language if language != "zh" else "zh"
    files = {"file": (filename or "utterance.wav", audio, "audio/wav")}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
            response = await client.post(url, headers=headers, data=data, files=files)
    except httpx.HTTPError as exc:
        raise VoiceNetworkError(f"网络识别无响应：{exc}", code="network_stt_unreachable") from exc
    if response.status_code >= 400:
        detail = response.text[:240]
        raise VoiceNetworkError(
            f"网络识别失败（HTTP {response.status_code}）{(': ' + detail) if detail else ''}",
            code="network_stt_failed",
            status_code=502,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise VoiceNetworkError("网络识别返回了非 JSON", code="network_stt_bad_response") from exc
    text = str(payload.get("text") or "").strip()
    return {
        "text": text,
        "raw_text": text,
        "language": language,
        "bytes": len(audio),
        "engine": "network",
        "provider": cfg.provider,
        "model": cfg.stt_model,
        "source": cfg.source,
    }


async def network_synthesize(
    text: str,
    *,
    config: VoiceNetworkConfig | None = None,
) -> bytes:
    cfg = config or resolve_voice_network_config()
    if not cfg.ready:
        raise VoiceNetworkError("网络朗读未配置", code="network_tts_unavailable", status_code=503)
    url = _audio_path(cfg.base_url or "", "speech")
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": cfg.tts_model or DEFAULT_OPENAI_TTS_MODEL,
        "input": text,
        "voice": cfg.tts_voice or DEFAULT_OPENAI_TTS_VOICE,
        "response_format": "wav",
    }
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise VoiceNetworkError(f"网络朗读无响应：{exc}", code="network_tts_unreachable") from exc
    if response.status_code >= 400:
        detail = response.text[:240]
        raise VoiceNetworkError(
            f"网络朗读失败（HTTP {response.status_code}）{(': ' + detail) if detail else ''}",
            code="network_tts_failed",
            status_code=502,
        )
    content = response.content
    if len(content) < 44:
        raise VoiceNetworkError("网络朗读返回了空音频", code="network_tts_empty")
    return content
