from __future__ import annotations

import asyncio

import pytest
from app.services.computer_use_consent import (
    ComputerUseConsentError,
    conversation_computer_use_approval,
    require_computer_use_consent,
)
from app.services.computer_use_service import ComputerUseError, ComputerUseService


def _screenshot() -> dict[str, object]:
    return {
        "image_data_url": "data:image/png;base64,AA==",
        "width": 800,
        "height": 450,
        "display_width_px": 800,
        "display_height_px": 450,
        "native_width": 1600,
        "native_height": 900,
        "origin_x": 100,
        "origin_y": 50,
        "scale_x": 2.0,
        "scale_y": 2.0,
    }


@pytest.mark.asyncio
async def test_computer_protocol_maps_screenshot_coordinates_and_verifies_actions(monkeypatch):
    service = ComputerUseService()
    cursor = {"x": 120, "y": 80}
    moved: list[tuple[int, int]] = []

    monkeypatch.setattr(service, "platform_available", lambda: True)
    monkeypatch.setattr(service, "_capture_desktop_sync", _screenshot)
    monkeypatch.setattr(service, "_cursor_position_sync", lambda: dict(cursor))

    def move(point: tuple[int, int]):
        moved.append(point)
        cursor.update(x=point[0], y=point[1])
        return dict(cursor)

    monkeypatch.setattr(service, "_move_cursor_sync", move)

    observed = await service.execute_action("screenshot")
    assert observed["protocol"] == "computer_20251124"
    assert observed["verification"] == "observation"
    assert observed["screenshot"]["width"] == 800

    receipt = await service.execute_action("mouse_move", {"coordinate": [125, 75]})
    assert moved == [(350, 200)]
    assert receipt["cursor_after"] == {"x": 350, "y": 200}
    assert receipt["verification"] == "post_action_screenshot"
    assert receipt["screenshot"]["native_width"] == 1600

    history = service.action_history()
    assert history["latest_sequence"] == 4
    assert [event["phase"] for event in history["events"]] == [
        "running", "verified", "running", "verified",
    ]
    assert history["latest_receipt"]["action_id"] == receipt["action_id"]


@pytest.mark.asyncio
async def test_coordinate_action_requires_a_fresh_observation(monkeypatch):
    service = ComputerUseService()
    monkeypatch.setattr(service, "platform_available", lambda: True)
    monkeypatch.setattr(service, "_cursor_position_sync", lambda: {"x": 0, "y": 0})

    with pytest.raises(ComputerUseError, match="fresh screenshot"):
        await service.execute_action("mouse_move", {"coordinate": [10, 10]})

    history = service.action_history()
    assert history["events"][-1]["phase"] == "failed"


@pytest.mark.asyncio
async def test_takeover_interrupts_wait_and_releases_the_mouse(monkeypatch):
    service = ComputerUseService()
    releases: list[tuple[str, bool]] = []
    monkeypatch.setattr(service, "platform_available", lambda: True)
    monkeypatch.setattr(service, "_cursor_position_sync", lambda: {"x": 0, "y": 0})
    monkeypatch.setattr(
        service,
        "_mouse_button_sync",
        lambda button, pressed: releases.append((button, pressed)),
    )

    task = asyncio.create_task(service.execute_action("wait", {"duration": 10}))
    for _ in range(50):
        if service.action_history()["active_action_id"]:
            break
        await asyncio.sleep(0.01)

    cancelled = await service.cancel_active_action()
    assert cancelled["cancelled"] is True
    with pytest.raises(ComputerUseError, match="interrupted"):
        await task
    assert releases[-1] == ("left", False)
    assert service.action_history()["events"][-1]["phase"] == "interrupted"


@pytest.mark.asyncio
async def test_chat_approval_does_not_skip_high_risk_native_consent(monkeypatch):
    monkeypatch.delenv("AETHERCHAT_TEST_AUTO_APPROVE_COMPUTER_USE", raising=False)
    monkeypatch.delenv("AETHERCHAT_COMPUTER_CONSENT_URL", raising=False)
    monkeypatch.delenv("AETHERCHAT_COMPUTER_CONSENT_TOKEN", raising=False)
    with conversation_computer_use_approval():
        await require_computer_use_consent("观察屏幕", "screenshot", risk="screen_read")
        with pytest.raises(ComputerUseConsentError, match="未获授权"):
            await require_computer_use_consent("写入本机文档", "notes.txt", risk="file_write")
        with pytest.raises(ComputerUseConsentError, match="未获授权"):
            await require_computer_use_consent("控制当前桌面", "left_click", risk="computer_control")
