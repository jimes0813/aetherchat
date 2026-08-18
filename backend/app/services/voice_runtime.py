from __future__ import annotations

import asyncio
import io
import math
import os
import re
import shutil
import sys
import tempfile
import wave
from array import array
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TTS_CHARS = 800
PCM_ACTIVITY_RMS = 0.006
MIN_PCM_ACTIVITY_MS = 180
WHISPER_NAMES = {"whisper-cli", "main"}
PIPER_NAMES = {"piper"}
FILLER_RE = re.compile(r"^(?:嗯|啊|呃|那个|就是|然后|uh|um|ah)[，,。.\s]*", re.IGNORECASE)
BRACKET_RE = re.compile(r"\[(?:blank_audio|silence|music|noise|inaudible).*?\]", re.IGNORECASE)
PAREN_NOISE_RE = re.compile(r"\((?:silence|blank_audio|music|noise)\)", re.IGNORECASE)


class VoiceRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hints: list[str] | None = None,
        status_code: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.hints = list(hints or [])
        self.status_code = status_code

    def as_detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": str(self), "code": self.code or "voice_error"}
        if self.hints:
            payload["hints"] = self.hints
        return payload


def _data_dir() -> Path:
    return Path(os.environ.get("AETHERCHAT_DATA_DIR", Path.home() / ".local/share/aetherchat"))


def _tmp_dir() -> Path:
    path = _data_dir() / "voice" / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _first_existing(candidates: list[str | None], *, executable: bool = False) -> Path | None:
    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser()
        try:
            # Keep the final executable symlink intact. Invoking a virtualenv's
            # ``bin/python`` through its resolved interpreter target loses the
            # virtualenv prefix and therefore all of its installed packages.
            path = path.parent.resolve() / path.name if executable else path.resolve()
        except OSError:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        if executable and not os.access(path, os.X_OK):
            continue
        return path
    return None


def default_whisper_cli() -> Path | None:
    return _first_existing(
        [
            os.environ.get("AETHERCHAT_WHISPER_CLI"),
            str(Path.home() / "whisper.cpp/build/bin/whisper-cli"),
            str(Path.home() / "whisper.cpp/build/bin/main"),
            shutil.which("whisper-cli"),
        ],
        executable=True,
    )


def default_whisper_model() -> Path | None:
    return _first_existing(
        [
            os.environ.get("AETHERCHAT_WHISPER_MODEL"),
            str(Path.home() / "whisper.cpp/models/ggml-small.bin"),
            str(Path.home() / "whisper.cpp/models/ggml-medium.bin"),
            str(Path.home() / "whisper.cpp/models/ggml-base.bin"),
        ]
    )


def default_piper_cli() -> Path | None:
    return _first_existing(
        [
            os.environ.get("AETHERCHAT_PIPER_CLI"),
            str(Path.home() / "piper/piper"),
            shutil.which("piper"),
        ],
        executable=True,
    )


def default_piper_voice() -> Path | None:
    return _first_existing(
        [
            os.environ.get("AETHERCHAT_PIPER_VOICE"),
            str(Path.home() / "piper/voices/zh_CN-huayan-medium.onnx"),
        ]
    )


def default_espeak_data() -> Path | None:
    env = os.environ.get("AETHERCHAT_ESPEAK_DATA")
    candidates = [
        Path(env).expanduser() if env else None,
        Path.home() / "piper/espeak-ng-data",
    ]
    for path in candidates:
        if path and path.is_dir():
            return path.resolve()
    return None


def clean_transcript(text: str) -> str:
    next_text = BRACKET_RE.sub(" ", text or "")
    next_text = PAREN_NOISE_RE.sub(" ", next_text)
    next_text = re.sub(r"\s+", " ", next_text).strip()
    while True:
        stripped = FILLER_RE.sub("", next_text).strip()
        if stripped == next_text:
            break
        next_text = stripped
    return next_text.strip("，,。.?？!！ ")


def looks_like_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def pcm_wav_has_speech(data: bytes) -> bool:
    """Validate browser WAV output and reject silence before Whisper can hallucinate."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                raise VoiceRuntimeError(
                    "仅接受 16-bit 单声道 PCM WAV 音频",
                    code="unsupported_audio_format",
                    hints=["前端应按 16 kHz 单声道 PCM 编码后再上传"],
                )
            sample_rate = wav.getframerate()
            if sample_rate < 8000 or sample_rate > 96000:
                raise VoiceRuntimeError("WAV 采样率无效", code="unsupported_sample_rate")
            frames = wav.readframes(wav.getnframes())
    except VoiceRuntimeError:
        raise
    except (EOFError, wave.Error) as exc:
        raise VoiceRuntimeError("WAV 音频结构损坏", code="malformed_audio") from exc

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    window_samples = max(1, round(sample_rate * 0.02))
    active_ms = 0.0
    for start in range(0, len(samples), window_samples):
        chunk = samples[start : start + window_samples]
        if not chunk:
            continue
        mean = sum(chunk) / len(chunk)
        rms = math.sqrt(sum((sample - mean) ** 2 for sample in chunk) / len(chunk)) / 32768
        if rms >= PCM_ACTIVITY_RMS:
            active_ms += (len(chunk) / sample_rate) * 1000
            if active_ms >= MIN_PCM_ACTIVITY_MS:
                return True
    return False


TtsEngine = Literal["auto", "piper", "cosyvoice"]


def default_cosyvoice_url() -> str:
    return os.environ.get("AETHERCHAT_COSYVOICE_URL", "http://127.0.0.1:7702").rstrip("/")


def default_cosyvoice_python() -> Path | None:
    return _first_existing(
        [
            os.environ.get("AETHERCHAT_COSYVOICE_PYTHON"),
            str(Path.home() / "cosyvoice/venv/bin/python"),
        ],
        executable=True,
    )


def default_cosyvoice_script() -> Path | None:
    return _first_existing(
        [
            os.environ.get("AETHERCHAT_COSYVOICE_SCRIPT"),
            str(Path.home() / "cosyvoice/tts_server.py"),
        ]
    )


def _loopback_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port or 7702
    except ValueError as exc:
        raise VoiceRuntimeError(
            "自然音色服务地址端口无效",
            code="invalid_loopback_tts",
            hints=["把 CosyVoice 地址改成 http://127.0.0.1:7702"],
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise VoiceRuntimeError(
            "自然音色服务只允许本机回环地址",
            code="non_loopback_tts",
            hints=["把 CosyVoice 地址改成 http://127.0.0.1:7702"],
        )
    return f"http://127.0.0.1:{port}"


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _cosy_log_path() -> Path:
    return _data_dir() / "voice" / "cosyvoice.log"


def _log_tail(path: Path, *, limit: int = 700) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit * 2))
            return handle.read().decode("utf-8", errors="replace").strip()[-limit:]
    except OSError:
        return ""


class VoiceRuntimeService:
    """Runs local STT/TTS binaries without a shell, on loopback-only assets."""

    def __init__(self) -> None:
        self._cosy_lock = asyncio.Lock()
        self._tts_lock = asyncio.Lock()
        self._cosy_process: asyncio.subprocess.Process | None = None

    async def _cosy_health(self) -> dict[str, Any] | None:
        try:
            base = _loopback_url(default_cosyvoice_url())
        except VoiceRuntimeError:
            return None
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1.5) as client:
                response = await client.get(f"{base}/health")
            if not response.is_success:
                return None
            payload = response.json()
            if payload.get("ok"):
                return {"url": base, "sample_rate": payload.get("sample_rate"), "spk": payload.get("spk")}
        except Exception:
            return None
        return None

    async def status(self) -> dict[str, Any]:
        whisper_cli = default_whisper_cli()
        whisper_model = default_whisper_model()
        piper_cli = default_piper_cli()
        piper_voice = default_piper_voice()
        espeak = default_espeak_data()
        cosy = await self._cosy_health()
        stt_ready = bool(whisper_cli and whisper_model)
        piper_ready = bool(piper_cli and piper_voice)
        tts_ready = piper_ready or bool(cosy)
        hints: list[str] = []
        if not stt_ready:
            hints.append("未找到 whisper-cli 或 ggml 模型，识别会不可用")
        if not piper_ready and not cosy:
            hints.append("未找到 Piper 或 CosyVoice，回复将只显示文字")
        elif not cosy:
            hints.append("CosyVoice 未运行时会用 Piper；可在语音设置里启动自然音色")
        return {
            "ready": stt_ready,
            "mode": "live",
            "stt": {
                "ready": stt_ready,
                "engine": "whisper.cpp",
                "executable": str(whisper_cli) if whisper_cli else None,
                "model": str(whisper_model) if whisper_model else None,
                "label": "Whisper Small · 本地",
                "acceleration": "cpu" if _env_enabled("AETHERCHAT_WHISPER_NO_GPU") else "gpu_if_available",
            },
            "tts": {
                "ready": tts_ready,
                "active": "cosyvoice" if cosy else ("piper" if piper_ready else None),
                "label": "Jarvis · CosyVoice 2" if cosy else ("华严 · 本地 Piper" if piper_ready else "未就绪"),
                "piper": {
                    "ready": piper_ready,
                    "engine": "piper",
                    "executable": str(piper_cli) if piper_cli else None,
                    "voice": str(piper_voice) if piper_voice else None,
                    "espeak_data": str(espeak) if espeak else None,
                    "label": "华严 · 本地 Piper",
                },
                "cosyvoice": {
                    "ready": bool(cosy),
                    "engine": "cosyvoice",
                    "url": cosy.get("url") if cosy else default_cosyvoice_url(),
                    "label": "Jarvis · CosyVoice 2",
                    "spk": cosy.get("spk") if cosy else "jarvis",
                    "startable": bool(default_cosyvoice_python() and default_cosyvoice_script()),
                    "managed": bool(self._cosy_process and self._cosy_process.returncode is None),
                    "log": str(_cosy_log_path()),
                },
            },
            "hints": hints,
            "privacy": "audio_stays_local",
        }

    async def transcribe(self, audio: bytes, *, language: str = "zh", filename: str = "utterance.wav") -> dict[str, Any]:
        if not audio:
            raise VoiceRuntimeError("没有收到音频", code="empty_audio")
        if len(audio) > MAX_AUDIO_BYTES:
            raise VoiceRuntimeError("录音超过 10 MB 限制", code="audio_too_large", status_code=413)
        if not looks_like_wav(audio):
            raise VoiceRuntimeError(
                "仅接受 WAV 音频（pcm_s16le）",
                code="unsupported_audio",
                hints=["前端应按 16 kHz 单声道 PCM 编码后再上传"],
            )
        lang = language if language in {"zh", "en", "auto"} else "zh"
        if not pcm_wav_has_speech(audio):
            return {
                "text": "",
                "raw_text": "",
                "language": lang,
                "bytes": len(audio),
                "no_speech": True,
            }
        whisper_cli = default_whisper_cli()
        whisper_model = default_whisper_model()
        if whisper_cli is None or whisper_model is None:
            raise VoiceRuntimeError(
                "本机识别引擎未就绪",
                code="stt_unavailable",
                hints=[
                    "把 whisper-cli 放到 ~/whisper.cpp/build/bin/whisper-cli",
                    "或设置 AETHERCHAT_WHISPER_CLI 与 AETHERCHAT_WHISPER_MODEL",
                ],
                status_code=503,
            )
        if whisper_cli.name not in WHISPER_NAMES:
            raise VoiceRuntimeError("识别程序文件名必须是 whisper-cli", code="bad_stt_executable")

        suffix = Path(filename).suffix.lower() if filename else ".wav"
        if suffix != ".wav":
            suffix = ".wav"
        with tempfile.NamedTemporaryFile(prefix="aether-stt-", suffix=suffix, dir=_tmp_dir(), delete=False) as tmp:
            tmp.write(audio)
            tmp_path = Path(tmp.name)
        try:
            command = [
                str(whisper_cli),
                "-m",
                str(whisper_model),
                "-f",
                str(tmp_path),
                "-l",
                "auto" if lang == "auto" else lang,
                "-nt",
                "-np",
                "-sns",
            ]
            if _env_enabled("AETHERCHAT_WHISPER_NO_GPU"):
                command.append("-ng")
            stdout, stderr, code = await self._run(command, timeout=90)
            if code != 0:
                raise VoiceRuntimeError(
                    "识别失败",
                    code="stt_failed",
                    hints=[stderr.strip()[:280] or "查看 whisper-cli 是否匹配当前架构"],
                )
            stdout_text = cast(str, stdout)
            text = clean_transcript(stdout_text)
            return {
                "text": text,
                "raw_text": stdout_text.strip(),
                "language": lang,
                "bytes": len(audio),
            }
        finally:
            tmp_path.unlink(missing_ok=True)

    async def synthesize(self, text: str, *, engine: TtsEngine = "auto") -> bytes:
        spoken = clean_transcript(text)
        if not spoken:
            raise VoiceRuntimeError("没有可朗读的文本", code="empty_text")
        if len(spoken) > MAX_TTS_CHARS:
            spoken = spoken[:MAX_TTS_CHARS].rstrip()
        if engine not in {"auto", "piper", "cosyvoice"}:
            engine = "auto"
        # Local speech engines are expensive and some are not re-entrant. A
        # bounded, ordered lane prevents a burst of streamed sentences from
        # starting many Piper/CosyVoice jobs at once.
        async with self._tts_lock:
            return await self._synthesize_locked(spoken, engine=engine)

    async def _synthesize_locked(self, spoken: str, *, engine: TtsEngine) -> bytes:
        if engine in {"auto", "cosyvoice"}:
            cosy = await self._cosy_health()
            if cosy:
                try:
                    return await self._synthesize_cosyvoice(spoken, cosy["url"])
                except VoiceRuntimeError:
                    if engine == "cosyvoice":
                        raise
            elif engine == "cosyvoice":
                raise VoiceRuntimeError(
                    "CosyVoice 未运行",
                    code="cosyvoice_unavailable",
                    hints=["在语音设置中启动自然音色，或先手动运行 ~/cosyvoice/tts_server.py"],
                    status_code=503,
                )
        return await self._synthesize_piper(spoken)

    async def _synthesize_cosyvoice(self, spoken: str, base: str) -> bytes:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=45.0) as client:
                response = await client.post(f"{base}/tts", json={"text": spoken, "speed": 1.0})
        except httpx.HTTPError as exc:
            raise VoiceRuntimeError("自然音色服务无响应", code="cosyvoice_unreachable", status_code=503) from exc
        if not response.is_success or not looks_like_wav(response.content):
            raise VoiceRuntimeError(
                "自然音色合成失败",
                code="cosyvoice_failed",
                hints=[response.text[:200] if response.text else "检查 CosyVoice 日志"],
            )
        return response.content

    async def _synthesize_piper(self, spoken: str) -> bytes:
        piper_cli = default_piper_cli()
        piper_voice = default_piper_voice()
        if piper_cli is None or piper_voice is None:
            raise VoiceRuntimeError(
                "本机朗读引擎未就绪",
                code="tts_unavailable",
                hints=[
                    "把 piper 和 zh_CN-huayan-medium.onnx 放到 ~/piper",
                    "或设置 AETHERCHAT_PIPER_CLI 与 AETHERCHAT_PIPER_VOICE",
                ],
                status_code=503,
            )
        if piper_cli.name not in PIPER_NAMES:
            raise VoiceRuntimeError("朗读程序文件名必须是 piper", code="bad_tts_executable")
        espeak = default_espeak_data()
        command = [str(piper_cli), "-m", str(piper_voice), "-f", "-", "-q"]
        if espeak is not None:
            command.extend(["--espeak_data", str(espeak)])
        env = os.environ.copy()
        lib_dir = str(piper_cli.parent)
        env["LD_LIBRARY_PATH"] = f"{lib_dir}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}"
        stdout, stderr, code = await self._run(
            command,
            timeout=45,
            stdin=spoken.encode("utf-8"),
            env=env,
        )
        stdout_bytes = cast(bytes, stdout)
        if code != 0 or not looks_like_wav(stdout_bytes):
            raise VoiceRuntimeError(
                "朗读失败",
                code="tts_failed",
                hints=[stderr.strip()[:280] or "确认 Piper 与 espeak-ng-data 完整"],
            )
        return stdout_bytes

    async def start_cosyvoice(self) -> dict[str, Any]:
        existing = await self._cosy_health()
        if existing:
            return {"ready": True, "started": False, "url": existing["url"], "label": "Jarvis · CosyVoice 2"}
        python = default_cosyvoice_python()
        script = default_cosyvoice_script()
        if python is None or script is None:
            raise VoiceRuntimeError(
                "未找到 CosyVoice 运行环境",
                code="cosyvoice_missing",
                hints=["确认 ~/cosyvoice/venv/bin/python 与 tts_server.py 存在"],
                status_code=503,
            )
        if script.name != "tts_server.py":
            raise VoiceRuntimeError("自然音色脚本文件名必须是 tts_server.py", code="bad_cosyvoice_script")
        async with self._cosy_lock:
            existing = await self._cosy_health()
            if existing:
                return {"ready": True, "started": False, "url": existing["url"], "label": "Jarvis · CosyVoice 2"}
            if self._cosy_process and self._cosy_process.returncode is None:
                process = self._cosy_process
            else:
                base = _loopback_url(default_cosyvoice_url())
                env = os.environ.copy()
                env["XINGHUO_TTS_PORT"] = str(urlparse(base).port or 7702)
                log_path = _cosy_log_path()
                log_path.parent.mkdir(parents=True, exist_ok=True)
                handle = log_path.open("ab", buffering=0)
                try:
                    marker = f"\n=== AetherChat CosyVoice start {datetime.now(UTC).isoformat()} ===\n"
                    handle.write(marker.encode("utf-8"))
                    process = await asyncio.create_subprocess_exec(
                        str(python),
                        str(script),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=handle,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=str(script.parent),
                        env=env,
                        start_new_session=True,
                    )
                finally:
                    handle.close()
                self._cosy_process = process
            deadline = asyncio.get_running_loop().time() + 180
            while asyncio.get_running_loop().time() < deadline:
                health = await self._cosy_health()
                if health:
                    return {
                        "ready": True,
                        "started": True,
                        "pid": process.pid,
                        "url": health["url"],
                        "label": "Jarvis · CosyVoice 2",
                    }
                if process.returncode is not None:
                    self._cosy_process = None
                    tail = _log_tail(_cosy_log_path())
                    raise VoiceRuntimeError(
                        "CosyVoice 启动失败",
                        code="cosyvoice_exited",
                        hints=[
                            f"查看 {_cosy_log_path()}",
                            *([tail] if tail else []),
                        ],
                    )
                await asyncio.sleep(1.0)
            raise VoiceRuntimeError(
                "CosyVoice 在 180 秒内未就绪",
                code="cosyvoice_timeout",
                hints=["首次加载模型较慢，可稍后再试或查看日志"],
                status_code=504,
            )

    async def shutdown(self) -> dict[str, Any]:
        """Stop only the CosyVoice process started by this backend instance."""
        async with self._cosy_lock:
            process = self._cosy_process
            self._cosy_process = None
            if process is None or process.returncode is not None:
                return {"stopped": False}
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
            return {"stopped": True, "returncode": process.returncode}

    async def _run(
        self,
        command: list[str],
        *,
        timeout: float,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[bytes | str, str, int]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise VoiceRuntimeError(
                "无法启动语音引擎",
                code="voice_engine_start_failed",
                hints=[str(exc)[:280]],
                status_code=503,
            ) from exc
        try:
            out, err = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
        except asyncio.CancelledError:
            process.kill()
            with suppress(Exception):
                await process.wait()
            raise
        except TimeoutError as exc:
            process.kill()
            with suppress(Exception):
                await process.wait()
            raise VoiceRuntimeError("语音引擎超时", code="voice_timeout", status_code=504) from exc
        stderr = err.decode("utf-8", errors="replace")
        if command and Path(command[0]).name in WHISPER_NAMES:
            return out.decode("utf-8", errors="replace"), stderr, int(process.returncode or 0)
        return out, stderr, int(process.returncode or 0)


voice_runtime = VoiceRuntimeService()
