from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx


class ComputerUseConsentError(PermissionError):
    pass


_conversation_approval: ContextVar[bool] = ContextVar("aetherchat_conversation_approval", default=False)


@contextmanager
def conversation_computer_use_approval():
    """Marks one orchestrator-controlled call as approved in the chat UI."""
    token = _conversation_approval.set(True)
    try:
        yield
    finally:
        _conversation_approval.reset(token)


async def require_computer_use_consent(
    action: str,
    detail: str,
    *,
    risk: str = "computer_control",
) -> None:
    """Ask the Electron main process for native, fail-closed approval.

    The random broker token and URL are inherited only by the bundled backend.
    A standalone backend has no desktop authority and therefore cannot approve
    model-driven Computer Use.
    """

    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("AETHERCHAT_TEST_AUTO_APPROVE_COMPUTER_USE") == "1"
    ):
        return
    # Chat-card approval may skip native prompts only for low-risk observation.
    # Keyboard/mouse, file writes and app launches always go through Electron.
    if _conversation_approval.get() and risk in {"screen_read", "file_read"}:
        return

    url = os.environ.get("AETHERCHAT_COMPUTER_CONSENT_URL", "").strip()
    token = os.environ.get("AETHERCHAT_COMPUTER_CONSENT_TOKEN", "").strip()
    if not url or not token:
        raise ComputerUseConsentError(
            "模型电脑控制未获授权：请从 AetherChat 桌面应用运行，并在本机确认权限。"
        )

    payload: dict[str, Any] = {
        "action": str(action)[:80],
        "detail": str(detail)[:2000],
        "risk": str(risk)[:80],
    }
    try:
        async with httpx.AsyncClient(timeout=65.0, trust_env=False) as client:
            response = await client.post(
                url,
                headers={"X-Aether-Consent-Token": token},
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ComputerUseConsentError(f"本机授权服务不可用：{exc}") from exc
    if result.get("approved") is not True:
        raise ComputerUseConsentError("用户拒绝了本次模型电脑控制操作。")
