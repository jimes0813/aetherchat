import asyncio
import io
import math
import os
import sys
import tempfile
import wave
from array import array
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-voice-tests-"))

from app.services.voice_runtime import (  # noqa: E402
    VoiceRuntimeError,
    VoiceRuntimeService,
    _first_existing,
    _loopback_url,
    clean_transcript,
    looks_like_wav,
    pcm_wav_has_speech,
)


def _pcm_wav(*, amplitude: float, seconds: float = 0.6, sample_rate: int = 16000) -> bytes:
    level = max(-1.0, min(1.0, amplitude)) * 32767
    samples = array(
        "h",
        (
            round(level * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(round(seconds * sample_rate))
        ),
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


def test_clean_transcript_strips_fillers_and_noise_tags():
    assert clean_transcript("[BLANK_AUDIO]") == ""
    assert clean_transcript("嗯，那个，帮我看看这段报错") == "帮我看看这段报错"
    assert clean_transcript("  你好。  ") == "你好"


def test_looks_like_wav_requires_riff_header():
    assert looks_like_wav(b"RIFF....WAVE....")
    assert not looks_like_wav(b"ID3mp3")


def test_pcm_activity_gate_rejects_silence_and_accepts_speech_level_audio():
    assert pcm_wav_has_speech(_pcm_wav(amplitude=0)) is False
    assert pcm_wav_has_speech(_pcm_wav(amplitude=0.02)) is True


def test_executable_discovery_preserves_virtualenv_python_symlink(tmp_path):
    interpreter = tmp_path / "python3.11"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o755)
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    venv_python = venv_bin / "python"
    venv_python.symlink_to(interpreter)

    discovered = _first_existing([str(venv_python)], executable=True)

    assert discovered == venv_python
    assert discovered != interpreter.resolve()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:7702", "http://127.0.0.1:7702"),
        ("http://localhost:8800/", "http://127.0.0.1:8800"),
        ("http://[::1]:9900", "http://127.0.0.1:9900"),
    ],
)
def test_loopback_url_normalizes_safe_local_addresses(raw, expected):
    assert _loopback_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://127.0.0.1:7702",
        "http://example.com:7702",
        "http://127.0.0.1:99999",
        "http://user@127.0.0.1:7702",
        "http://127.0.0.1:7702/other",
    ],
)
def test_loopback_url_rejects_unsafe_or_ambiguous_addresses(raw):
    with pytest.raises(VoiceRuntimeError):
        _loopback_url(raw)


@pytest.mark.asyncio
async def test_transcribe_rejects_non_wav():
    runtime = VoiceRuntimeService()
    with pytest.raises(VoiceRuntimeError, match="WAV"):
        await runtime.transcribe(b"not-audio")


@pytest.mark.asyncio
async def test_transcribe_reports_missing_engine(monkeypatch):
    runtime = VoiceRuntimeService()
    monkeypatch.setattr("app.services.voice_runtime.default_whisper_cli", lambda: None)
    monkeypatch.setattr("app.services.voice_runtime.default_whisper_model", lambda: None)
    with pytest.raises(VoiceRuntimeError, match="识别引擎"):
        await runtime.transcribe(_pcm_wav(amplitude=0.02))


@pytest.mark.asyncio
async def test_transcribe_short_circuits_silence_before_loading_whisper(monkeypatch):
    runtime = VoiceRuntimeService()

    def should_not_probe():
        raise AssertionError("silent audio must not load Whisper")

    monkeypatch.setattr("app.services.voice_runtime.default_whisper_cli", should_not_probe)
    result = await runtime.transcribe(_pcm_wav(amplitude=0))

    assert result["text"] == ""
    assert result["no_speech"] is True


@pytest.mark.asyncio
async def test_transcribe_uses_available_gpu_unless_cpu_is_explicitly_requested(monkeypatch, tmp_path):
    runtime = VoiceRuntimeService()
    whisper = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    commands = []

    async def fake_run(command, **_kwargs):
        commands.append(command)
        return "你好", "", 0

    monkeypatch.setattr("app.services.voice_runtime.default_whisper_cli", lambda: whisper)
    monkeypatch.setattr("app.services.voice_runtime.default_whisper_model", lambda: model)
    monkeypatch.setattr("app.services.voice_runtime._tmp_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.delenv("AETHERCHAT_WHISPER_NO_GPU", raising=False)

    await runtime.transcribe(_pcm_wav(amplitude=0.02))
    assert "-ng" not in commands[-1]

    monkeypatch.setenv("AETHERCHAT_WHISPER_NO_GPU", "1")
    await runtime.transcribe(_pcm_wav(amplitude=0.02))
    assert "-ng" in commands[-1]


@pytest.mark.asyncio
async def test_tts_jobs_are_serialized(monkeypatch):
    runtime = VoiceRuntimeService()
    active = 0
    peak = 0

    async def fake_synthesize(_spoken, *, engine):
        nonlocal active, peak
        assert engine == "piper"
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return b"RIFF....WAVE...."

    monkeypatch.setattr(runtime, "_synthesize_locked", fake_synthesize)
    await asyncio.gather(
        runtime.synthesize("第一句", engine="piper"),
        runtime.synthesize("第二句", engine="piper"),
    )
    assert peak == 1


@pytest.mark.asyncio
async def test_shutdown_stops_only_the_managed_cosyvoice_process():
    runtime = VoiceRuntimeService()

    class FakeProcess:
        returncode = None

        def __init__(self):
            self.terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def wait(self):
            return self.returncode

    process = FakeProcess()
    runtime._cosy_process = process

    result = await runtime.shutdown()

    assert result == {"stopped": True, "returncode": -15}
    assert process.terminated is True
    assert runtime._cosy_process is None


@pytest.mark.asyncio
async def test_synthesize_rejects_empty_text():
    runtime = VoiceRuntimeService()
    with pytest.raises(VoiceRuntimeError, match="可朗读"):
        await runtime.synthesize("嗯，")


@pytest.mark.asyncio
async def test_status_includes_privacy_and_mode():
    status = await VoiceRuntimeService().status()
    assert status["mode"] == "live"
    assert status["privacy"] == "audio_stays_local"
    assert "stt" in status and "tts" in status
    assert "piper" in status["tts"]
    assert "cosyvoice" in status["tts"]


@pytest.mark.asyncio
async def test_status_shape_does_not_depend_on_prompt_bias():
    status = await VoiceRuntimeService().status()
    assert status["stt"]["engine"] == "whisper.cpp"
