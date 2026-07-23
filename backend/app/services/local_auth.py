from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import urlparse

from fastapi import Header, HTTPException, WebSocket, status

_DEMO_API_TOKEN = secrets.token_hex(32)
_TRUTHY = {"1", "true", "yes", "on"}


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def allow_unauthenticated() -> bool:
    """Explicit test-only escape hatch; never enabled by default."""
    return _enabled("AETHERCHAT_ALLOW_UNAUTHENTICATED")


def demo_auth_enabled() -> bool:
    return _enabled("AETHERCHAT_ENABLE_DEMO_AUTH")


def effective_api_token() -> str:
    configured = os.environ.get("AETHERCHAT_API_TOKEN", "").strip()
    if configured:
        return configured
    return _DEMO_API_TOKEN if demo_auth_enabled() else ""


def token_is_valid(candidate: str | None) -> bool:
    if allow_unauthenticated():
        return True
    expected = effective_api_token()
    supplied = str(candidate or "")
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


async def require_api_access(authorization: str | None = Header(default=None)) -> None:
    if allow_unauthenticated():
        return
    if not effective_api_token():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local API authentication is not configured",
        )
    if not token_is_valid(bearer_token(authorization)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid local API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def websocket_token(websocket: WebSocket) -> tuple[str, str | None]:
    offered = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    prefix = "aetherchat.auth."
    token = next((item[len(prefix):] for item in offered if item.startswith(prefix)), "")
    selected = "aetherchat" if "aetherchat" in offered else None
    return token, selected


def websocket_origin_is_allowed(websocket: WebSocket) -> bool:
    if allow_unauthenticated():
        return True
    origin = websocket.headers.get("origin")
    if not origin or origin in {"null", "file://"}:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def demo_token_response() -> dict[str, str]:
    if not demo_auth_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demo authentication is disabled")
    return {"access_token": effective_api_token(), "token_type": "bearer"}
