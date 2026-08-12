from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from contextlib import suppress
from datetime import UTC, datetime
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from ..request_context import current_request_id

_FALSE_VALUES = {"0", "false", "no", "off"}
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"client[_ -]?secret|authorization|password|token|secret)"
    r"(\s*[=:]\s*)"
    r"([^\s,;]+)"
)
_DATA_URL_RE = re.compile(r"(?i)data:[^,\s]+;base64,[A-Za-z0-9+/=]+")
_URL_QUERY_RE = re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+")
_ROUTE_QUERY_RE = re.compile(r"(?<!\w)(/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+)\?[^\s]+")
_POSIX_USER_PATH_RE = re.compile(r"/(?:home|Users)/[^\s\"'<>]+")
_WINDOWS_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\s\"'<>]+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SAFE_LOG_FIELDS = (
    "event",
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "exception_type",
    "trace_id",
    "run_status",
)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _data_root() -> Path:
    configured = os.environ.get("AETHERCHAT_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aetherchat"


def structured_log_path(data_root: str | os.PathLike[str] | None = None) -> Path:
    root = Path(data_root).expanduser().resolve() if data_root else _data_root()
    return root / "logs" / "aetherchat.jsonl"


def diagnostic_ref(kind: str, value: object) -> str:
    """Return a stable correlation reference without exposing the source ID."""

    normalized_kind = re.sub(r"[^a-z0-9_-]", "", str(kind).lower())[:20] or "id"
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]
    return f"{normalized_kind}:{digest}"


def sanitize_log_text(value: object, *, max_length: int = 2000) -> str:
    """Return a single-line diagnostic string without common secret/path forms."""

    text = " ".join(str(value or "").replace("\x00", " ").splitlines())
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[redacted]", text)
    text = _DATA_URL_RE.sub("[data-url-redacted]", text)
    text = _URL_QUERY_RE.sub(r"\1?[query-redacted]", text)
    text = _ROUTE_QUERY_RE.sub(r"\1?[query-redacted]", text)
    text = _POSIX_USER_PATH_RE.sub("[local-path]", text)
    text = _WINDOWS_USER_PATH_RE.sub("[local-path]", text)
    text = _UUID_RE.sub("[id]", text)
    return text[:max_length]


class JsonLineFormatter(logging.Formatter):
    """Conservative JSON logger that never serializes tracebacks or request bodies."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": sanitize_log_text(record.name, max_length=200),
            "message": sanitize_log_text(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if request_id:
            payload["request_id"] = sanitize_log_text(request_id, max_length=128)
            payload["request_ref"] = diagnostic_ref("request", request_id)
        for field in _SAFE_LOG_FIELDS:
            if field == "request_id":
                continue
            value = getattr(record, field, None)
            if value is None:
                continue
            if isinstance(value, bool | int | float):
                payload[field] = value
            else:
                payload[field] = sanitize_log_text(value, max_length=200)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self) -> TextIOWrapper:
        stream = super()._open()
        with suppress(OSError):
            Path(self.baseFilename).chmod(0o600)
        return stream


def configure_structured_logging(
    data_root: str | os.PathLike[str] | None = None,
    *,
    logger_name: str = "app",
) -> Path | None:
    if os.environ.get("AETHERCHAT_JSON_LOGS", "1").strip().lower() in _FALSE_VALUES:
        return None
    path = structured_log_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        return None
    with suppress(OSError):
        path.parent.chmod(0o700)
    resolved = str(path.resolve())
    handler_name = f"aetherchat-json:{resolved}"
    target = logging.getLogger(logger_name)
    for handler in target.handlers:
        if handler.get_name() == handler_name:
            return path

    handler = _PrivateRotatingFileHandler(
        path,
        maxBytes=_bounded_int(
            "AETHERCHAT_JSON_LOG_MAX_BYTES",
            2 * 1024 * 1024,
            64 * 1024,
            20 * 1024 * 1024,
        ),
        backupCount=_bounded_int("AETHERCHAT_JSON_LOG_BACKUPS", 3, 1, 10),
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter())
    handler.set_name(handler_name)
    target.addHandler(handler)
    if target.level == logging.NOTSET or target.level > logging.INFO:
        target.setLevel(logging.INFO)
    with suppress(OSError):
        path.chmod(0o600)
    return path


def read_recent_log_entries(
    path: str | os.PathLike[str] | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read only allowlisted, re-sanitized JSON entries from local rotations."""

    target = Path(path).expanduser().absolute() if path else structured_log_path()
    if target.parent.is_symlink() or target.is_symlink():
        return []
    bounded_limit = max(1, min(int(limit), 500))
    candidates = [target.with_name(f"{target.name}.{index}") for index in range(10, 0, -1)]
    candidates.append(target)
    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                raw = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            safe: dict[str, Any] = {}
            for field in (
                "timestamp",
                "level",
                "logger",
                "message",
                "request_ref",
                *_SAFE_LOG_FIELDS,
            ):
                value = raw.get(field)
                if value is None:
                    continue
                if field in {"status_code", "duration_ms"} and isinstance(value, int | float):
                    safe[field] = value
                else:
                    safe[field] = sanitize_log_text(value)
            if safe:
                entries.append(safe)
    return entries[-bounded_limit:]
