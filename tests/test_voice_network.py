import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-voice-net-"))

from app.services.voice_network import (  # noqa: E402
    VoiceNetworkConfig,
    _audio_path,
    _host_kind,
    resolve_voice_network_config,
    voice_provider_preference,
)


def test_audio_path_joins_openai_style_base():
    assert _audio_path("https://api.openai.com/v1", "transcriptions").endswith("/audio/transcriptions")
    assert _audio_path("https://open.bigmodel.cn/api/paas/v4/", "speech").endswith("/audio/speech")


def test_host_kind_detects_zhipu_and_openai():
    assert _host_kind("https://open.bigmodel.cn/api/paas/v4") == "zhipu"
    assert _host_kind("https://api.openai.com/v1") == "openai"


def test_voice_provider_preference_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("AETHERCHAT_VOICE_STT_PROVIDER", raising=False)
    assert voice_provider_preference("stt") == "auto"
    monkeypatch.setenv("AETHERCHAT_VOICE_TTS_PROVIDER", "network")
    assert voice_provider_preference("tts") == "network"


def test_resolve_config_from_env(monkeypatch):
    monkeypatch.setenv("AETHERCHAT_VOICE_API_KEY", "sk-test")
    monkeypatch.setenv("AETHERCHAT_VOICE_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.delenv("AETHERCHAT_VOICE_STT_MODEL", raising=False)
    cfg = resolve_voice_network_config()
    assert cfg.ready
    assert cfg.provider == "zhipu"
    assert cfg.stt_model == "glm-asr-2512"
    assert isinstance(cfg, VoiceNetworkConfig)
