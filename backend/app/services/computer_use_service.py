from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import io
import math
import os
import shutil
import subprocess
import time
from collections import OrderedDict, deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

MAX_ACCESSIBILITY_ELEMENTS = 300
MAX_SNAPSHOTS = 64
MAX_TEXT_INPUT = 20_000
MAX_DIRECTORY_ITEMS = 500
SNAPSHOT_TTL_SECONDS = 15 * 60
VIEWPORT_TTL_SECONDS = 2 * 60


class ComputerUseError(ValueError):
    pass


@dataclass
class _Snapshot:
    window_id: int | None
    created_at: float
    elements: list[dict[str, Any]]


def _initialize_dpi_awareness() -> None:
    if os.name != "nt":
        return
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return
    try:
        windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        with suppress(Exception):
            windll.user32.SetProcessDPIAware()


_initialize_dpi_awareness()


class ComputerUseService:
    """Local observation/action runtime used by the trusted built-in plugin.

    The service never decides whether an action is allowed. The plugin dispatcher
    obtains approval from Electron's private consent broker before calling here.
    """

    ACTIONS = (
        "screenshot",
        "mouse_move",
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "triple_click",
        "left_click_drag",
        "left_mouse_down",
        "left_mouse_up",
        "scroll",
        "type",
        "key",
        "hold_key",
        "wait",
        "zoom",
        "cursor_position",
    )

    def __init__(self) -> None:
        self._snapshots: OrderedDict[str, _Snapshot] = OrderedDict()
        self._action_lock = asyncio.Lock()
        self._cancel_event: asyncio.Event | None = None
        self._active_action_id: str | None = None
        self._last_viewport: dict[str, Any] | None = None
        self._last_viewport_at = 0.0
        self._action_history: deque[dict[str, Any]] = deque(maxlen=64)
        self._action_sequence = 0
        self._latest_receipt: dict[str, Any] | None = None

    @classmethod
    def supported_actions(cls) -> list[str]:
        return list(cls.ACTIONS)

    @staticmethod
    def platform_available() -> bool:
        if os.name == "nt":
            return True
        try:
            from .x11_desktop import available

            return available()
        except (ImportError, OSError):
            return False

    async def capabilities(self) -> dict[str, Any]:
        available = await asyncio.to_thread(self.platform_available)
        return {
            "available": available,
            "platform": "windows" if os.name == "nt" else "linux-x11",
            "type": "computer_20251124",
            "actions": self.supported_actions(),
            "enable_zoom": True,
            "post_action_verification": True,
            "interruptible": True,
            "coordinate_space": "latest_screenshot",
        }

    @staticmethod
    def _pair(value: Any, name: str) -> tuple[int, int]:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(isinstance(item, bool) for item in value)
        ):
            raise ComputerUseError(f"{name} must contain exactly two integer coordinates")
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError) as exc:
            raise ComputerUseError(f"{name} must contain exactly two integer coordinates") from exc

    def _map_screenshot_coordinate(self, value: Any, name: str = "coordinate") -> tuple[int, int]:
        x, y = self._pair(value, name)
        viewport = self._last_viewport
        if viewport is None or time.monotonic() - self._last_viewport_at > VIEWPORT_TTL_SECONDS:
            self._last_viewport = None
            raise ComputerUseError("Take a fresh screenshot before issuing a coordinate action")
        width = int(viewport["width"])
        height = int(viewport["height"])
        if not (0 <= x < width and 0 <= y < height):
            raise ComputerUseError(
                f"{name} ({x}, {y}) is outside the latest {width}×{height} screenshot"
            )
        native_x = int(viewport["origin_x"]) + round(x * float(viewport["scale_x"]))
        native_y = int(viewport["origin_y"]) + round(y * float(viewport["scale_y"]))
        return native_x, native_y

    @staticmethod
    def _screen_bounds_sync() -> dict[str, int]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                return x11_desktop.screen_bounds()
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api
        import win32con

        return {
            "left": win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN),
            "top": win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN),
            "width": win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN),
            "height": win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN),
        }

    @classmethod
    def _capture_desktop_sync(cls) -> dict[str, Any]:
        bounds = cls._screen_bounds_sync()
        return cls._capture_region_sync(
            bounds["left"],
            bounds["top"],
            bounds["width"],
            bounds["height"],
        )

    @staticmethod
    def _cursor_position_sync() -> dict[str, int]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                return x11_desktop.cursor_position()
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api

        x, y = win32api.GetCursorPos()
        return {"x": int(x), "y": int(y)}

    @staticmethod
    def _move_cursor_sync(point: tuple[int, int]) -> dict[str, int]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                return x11_desktop.move_cursor(*point)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api

        bounds = ComputerUseService._screen_bounds_sync()
        if not (
            bounds["left"] <= point[0] < bounds["left"] + bounds["width"]
            and bounds["top"] <= point[1] < bounds["top"] + bounds["height"]
        ):
            raise ComputerUseError("Target coordinates are outside the virtual desktop")
        win32api.SetCursorPos(point)
        return {"x": point[0], "y": point[1]}

    @staticmethod
    def _mouse_button_sync(button: str, pressed: bool) -> None:
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.mouse_button(button, pressed)
                return
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api
        import win32con

        flags = {
            "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
            "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
            "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
        }
        if button not in flags:
            raise ComputerUseError("Unsupported mouse button")
        win32api.mouse_event(flags[button][0 if pressed else 1], 0, 0, 0, 0)

    @classmethod
    def _screen_click_sync(cls, button: str, count: int) -> dict[str, Any]:
        normalized_count = int(count)
        if not 1 <= normalized_count <= 3:
            raise ComputerUseError("click_count must be between 1 and 3")
        for index in range(normalized_count):
            cls._mouse_button_sync(button, True)
            cls._mouse_button_sync(button, False)
            if index + 1 < normalized_count:
                time.sleep(0.08)
        return {"clicked": True, "button": button, "click_count": normalized_count}

    @staticmethod
    def _type_screen_sync(text: str) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.type_text(text)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        else:
            ComputerUseService._send_unicode_text(text)
        return {"typed": True, "character_count": len(text)}

    @staticmethod
    def _press_screen_key_sync(key: str) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.press_key(key)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
            return {"pressed": True, "key": key}
        import win32gui

        hwnd = int(win32gui.GetForegroundWindow())
        if hwnd <= 0:
            raise ComputerUseError("No foreground window is available for keyboard input")
        return ComputerUseService._press_key_sync(hwnd, key)

    @staticmethod
    def _key_down_sync(key: str) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                return {"platform": "x11", "codes": x11_desktop.key_down(key), "key": key}
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api
        import win32con

        aliases = {
            "ctrl": win32con.VK_CONTROL, "control": win32con.VK_CONTROL,
            "control_l": win32con.VK_CONTROL, "shift": win32con.VK_SHIFT,
            "shift_l": win32con.VK_SHIFT, "alt": win32con.VK_MENU,
            "alt_l": win32con.VK_MENU, "win": win32con.VK_LWIN,
            "super": win32con.VK_LWIN,
        }
        named = {
            "return": win32con.VK_RETURN, "enter": win32con.VK_RETURN,
            "tab": win32con.VK_TAB, "escape": win32con.VK_ESCAPE,
            "esc": win32con.VK_ESCAPE, "space": win32con.VK_SPACE,
            "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE,
            "home": win32con.VK_HOME, "end": win32con.VK_END,
            "pageup": win32con.VK_PRIOR, "pagedown": win32con.VK_NEXT,
            "up": win32con.VK_UP, "down": win32con.VK_DOWN,
            "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
        }
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if not parts:
            raise ComputerUseError("Invalid key chord")
        codes: list[int] = []
        for part in parts:
            if part in aliases:
                code = aliases[part]
            elif part in named:
                code = named[part]
            elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
                code = win32con.VK_F1 + int(part[1:]) - 1
            elif len(part) == 1:
                code = ord(part.upper())
            else:
                raise ComputerUseError(f"Unsupported key: {part}")
            win32api.keybd_event(code, 0, 0, 0)
            codes.append(code)
        return {"platform": "windows", "codes": codes, "key": key}

    @staticmethod
    def _key_up_sync(token: dict[str, Any]) -> None:
        codes = [int(value) for value in token.get("codes") or []]
        if token.get("platform") == "x11":
            from . import x11_desktop

            try:
                x11_desktop.key_up_codes(codes)
                return
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api
        import win32con

        for code in reversed(codes):
            win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)

    @staticmethod
    def _scroll_screen_sync(delta_x: int, delta_y: int) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                return {"scrolled": True, **x11_desktop.scroll(delta_x, delta_y)}
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import win32api
        import win32con

        horizontal = max(-5_000, min(5_000, int(delta_x)))
        vertical = max(-5_000, min(5_000, int(delta_y)))
        if vertical:
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -vertical, 0)
        if horizontal and hasattr(win32con, "MOUSEEVENTF_HWHEEL"):
            win32api.mouse_event(win32con.MOUSEEVENTF_HWHEEL, 0, 0, horizontal, 0)
        return {"scrolled": True, "scroll_x": horizontal, "scroll_y": vertical}

    @staticmethod
    def _drag_screen_sync(
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int,
    ) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.drag(start, end, duration_ms)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        else:
            import win32api
            import win32con

            duration = max(100, min(5_000, int(duration_ms))) / 1000
            steps = max(5, min(100, int(duration * 60)))
            win32api.SetCursorPos(start)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            try:
                for step in range(1, steps + 1):
                    ratio = step / steps
                    win32api.SetCursorPos((
                        round(start[0] + (end[0] - start[0]) * ratio),
                        round(start[1] + (end[1] - start[1]) * ratio),
                    ))
                    time.sleep(duration / steps)
            finally:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return {"dragged": True, "from": list(start), "to": list(end), "duration_ms": int(duration_ms)}

    async def _wait_or_cancel(self, duration: float, event: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.0, duration))
        except TimeoutError:
            return
        raise ComputerUseError("Computer Use action was interrupted by the user")

    def _record_action_event(
        self,
        *,
        action_id: str,
        action: str,
        phase: str,
        at: str,
        duration_ms: int | None = None,
        detail: str | None = None,
    ) -> int:
        self._action_sequence += 1
        event: dict[str, Any] = {
            "sequence": self._action_sequence,
            "action_id": action_id,
            "action": action,
            "phase": phase,
            "at": at,
        }
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if detail:
            event["detail"] = str(detail)[:500]
        self._action_history.append(event)
        return self._action_sequence

    def action_history(self, after: int = 0) -> dict[str, Any]:
        cursor = max(0, int(after))
        events = [dict(item) for item in self._action_history if int(item["sequence"]) > cursor]
        latest_receipt = self._latest_receipt
        return {
            "events": events,
            "latest_sequence": self._action_sequence,
            "active_action_id": self._active_action_id,
            "latest_receipt": (
                latest_receipt["receipt"]
                if latest_receipt and int(latest_receipt["sequence"]) > cursor
                else None
            ),
        }

    async def execute_action(self, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute the public computer_20251124-compatible action contract.

        Coordinates always refer to the latest screenshot returned by this
        service.  Every mutating action returns a fresh verification screenshot,
        so the next model turn never has to reason from stale pixels.
        """

        normalized_action = str(action or "").strip().lower()
        if normalized_action not in self.ACTIONS:
            raise ComputerUseError(f"Unsupported computer action: {normalized_action}")
        if not await asyncio.to_thread(self.platform_available):
            raise ComputerUseError("Computer Use requires Windows or an active X11/XTest desktop")
        values = dict(arguments or {})
        action_id = f"computer_{uuid4().hex}"
        started_clock = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        async with self._action_lock:
            cancel_event = asyncio.Event()
            self._cancel_event = cancel_event
            self._active_action_id = action_id
            self._record_action_event(
                action_id=action_id,
                action=normalized_action,
                phase="running",
                at=started_at,
            )
            cursor_before = await asyncio.to_thread(self._cursor_position_sync)
            result: dict[str, Any] = {}
            screenshot: dict[str, Any] | None = None
            try:
                coordinate_value = values.get("coordinate")
                if normalized_action == "screenshot":
                    screenshot = await asyncio.to_thread(self._capture_desktop_sync)
                    result = {"observed": True}
                elif normalized_action == "cursor_position":
                    result = {"position": cursor_before}
                elif normalized_action == "mouse_move":
                    point = self._map_screenshot_coordinate(coordinate_value)
                    result = {"moved": True, "position": await asyncio.to_thread(self._move_cursor_sync, point)}
                elif normalized_action in {
                    "left_click", "right_click", "middle_click", "double_click", "triple_click",
                }:
                    if coordinate_value is not None:
                        point = self._map_screenshot_coordinate(coordinate_value)
                        await asyncio.to_thread(self._move_cursor_sync, point)
                    button = {
                        "right_click": "right",
                        "middle_click": "middle",
                    }.get(normalized_action, "left")
                    count = {"double_click": 2, "triple_click": 3}.get(normalized_action, 1)
                    modifier_token = None
                    modifiers = [str(value) for value in values.get("modifiers") or [] if str(value).strip()]
                    try:
                        if modifiers:
                            modifier_token = await asyncio.to_thread(self._key_down_sync, "+".join(modifiers))
                        result = await asyncio.to_thread(self._screen_click_sync, button, count)
                    finally:
                        if modifier_token is not None:
                            await asyncio.to_thread(self._key_up_sync, modifier_token)
                elif normalized_action in {"left_mouse_down", "left_mouse_up"}:
                    if coordinate_value is not None:
                        point = self._map_screenshot_coordinate(coordinate_value)
                        await asyncio.to_thread(self._move_cursor_sync, point)
                    pressed = normalized_action == "left_mouse_down"
                    await asyncio.to_thread(self._mouse_button_sync, "left", pressed)
                    result = {"button": "left", "pressed": pressed}
                elif normalized_action == "left_click_drag":
                    end = self._map_screenshot_coordinate(coordinate_value)
                    if values.get("start_coordinate") is not None:
                        start = self._map_screenshot_coordinate(values["start_coordinate"], "start_coordinate")
                    else:
                        current = await asyncio.to_thread(self._cursor_position_sync)
                        start = (current["x"], current["y"])
                    duration_ms = round(max(0.1, min(5.0, float(values.get("duration") or 0.5))) * 1000)
                    result = await asyncio.to_thread(self._drag_screen_sync, start, end, duration_ms)
                elif normalized_action == "scroll":
                    if coordinate_value is not None:
                        point = self._map_screenshot_coordinate(coordinate_value)
                        await asyncio.to_thread(self._move_cursor_sync, point)
                    delta_x = int(values.get("delta_x") or 0)
                    delta_y = int(values.get("delta_y") or 0)
                    direction = str(values.get("scroll_direction") or "").lower()
                    amount = max(0, min(5_000, int(values.get("scroll_amount") or 0)))
                    if direction:
                        signed = amount or 600
                        if direction == "up":
                            delta_y = -signed
                        elif direction == "down":
                            delta_y = signed
                        elif direction == "left":
                            delta_x = -signed
                        elif direction == "right":
                            delta_x = signed
                        else:
                            raise ComputerUseError("scroll_direction must be up, down, left, or right")
                    if not delta_x and not delta_y:
                        raise ComputerUseError("scroll requires a direction or non-zero delta")
                    result = await asyncio.to_thread(self._scroll_screen_sync, delta_x, delta_y)
                elif normalized_action == "type":
                    text = values.get("text")
                    if not isinstance(text, str) or not text or len(text) > MAX_TEXT_INPUT:
                        raise ComputerUseError(f"type text must contain 1–{MAX_TEXT_INPUT} characters")
                    result = await asyncio.to_thread(self._type_screen_sync, text)
                elif normalized_action == "key":
                    key = str(values.get("text") or "").strip()
                    if not key or len(key) > 100:
                        raise ComputerUseError("key requires a key name or chord up to 100 characters")
                    result = await asyncio.to_thread(self._press_screen_key_sync, key)
                elif normalized_action == "hold_key":
                    key = str(values.get("text") or "").strip()
                    if not key or len(key) > 100:
                        raise ComputerUseError("hold_key requires a key name or chord")
                    duration = max(0.1, min(10.0, float(values.get("duration") or 1.0)))
                    token = await asyncio.to_thread(self._key_down_sync, key)
                    try:
                        await self._wait_or_cancel(duration, cancel_event)
                    finally:
                        await asyncio.to_thread(self._key_up_sync, token)
                    result = {"held": True, "key": key, "duration": duration}
                elif normalized_action == "wait":
                    duration = max(0.0, min(30.0, float(values.get("duration") or 1.0)))
                    await self._wait_or_cancel(duration, cancel_event)
                    result = {"waited": True, "duration": duration}
                elif normalized_action == "zoom":
                    raw_region = values.get("region")
                    if not isinstance(raw_region, (list, tuple)) or len(raw_region) != 4:
                        raise ComputerUseError("zoom region must be [x1, y1, x2, y2]")
                    top_left = self._map_screenshot_coordinate(raw_region[:2], "region start")
                    bottom_right = self._map_screenshot_coordinate(raw_region[2:], "region end")
                    left, right = sorted((top_left[0], bottom_right[0]))
                    top, bottom = sorted((top_left[1], bottom_right[1]))
                    if right - left < 2 or bottom - top < 2:
                        raise ComputerUseError("zoom region is too small")
                    screenshot = await asyncio.to_thread(
                        self._capture_region_sync,
                        left,
                        top,
                        right - left,
                        bottom - top,
                    )
                    result = {"zoomed": True, "region": list(raw_region)}

                if cancel_event.is_set():
                    raise ComputerUseError("Computer Use action was interrupted by the user")
                mutating = normalized_action not in {"screenshot", "zoom", "cursor_position", "wait"}
                if mutating:
                    await asyncio.sleep(0.12)
                    screenshot = await asyncio.to_thread(self._capture_desktop_sync)
                if screenshot is not None and normalized_action != "zoom":
                    self._last_viewport = {
                        key: value for key, value in screenshot.items() if key != "image_data_url"
                    }
                    self._last_viewport_at = time.monotonic()
                cursor_after = await asyncio.to_thread(self._cursor_position_sync)
                completed_at = datetime.now(UTC).isoformat()
                receipt: dict[str, Any] = {
                    "protocol": "computer_20251124",
                    "action_id": action_id,
                    "action": normalized_action,
                    "status": "success",
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "duration_ms": round((time.monotonic() - started_clock) * 1000),
                    "result": result,
                    "cursor_before": cursor_before,
                    "cursor_after": cursor_after,
                    "verification": "post_action_screenshot" if mutating else "observation",
                }
                if screenshot is not None:
                    receipt["screenshot"] = screenshot
                sequence = self._record_action_event(
                    action_id=action_id,
                    action=normalized_action,
                    phase="verified",
                    at=completed_at,
                    duration_ms=int(receipt["duration_ms"]),
                    detail=str(receipt["verification"]),
                )
                self._latest_receipt = {"sequence": sequence, "receipt": receipt}
                return receipt
            except Exception as exc:
                message = str(exc)
                self._record_action_event(
                    action_id=action_id,
                    action=normalized_action,
                    phase="interrupted" if "interrupt" in message.casefold() else "failed",
                    at=datetime.now(UTC).isoformat(),
                    duration_ms=round((time.monotonic() - started_clock) * 1000),
                    detail=message,
                )
                raise
            finally:
                if self._active_action_id == action_id:
                    self._active_action_id = None
                    self._cancel_event = None

    async def cancel_active_action(self) -> dict[str, Any]:
        action_id = self._active_action_id
        event = self._cancel_event
        if event is not None:
            event.set()
        # Releasing the primary button is fail-safe if interruption arrives
        # during a drag or an explicit left_mouse_down action.
        with suppress(Exception):
            await asyncio.to_thread(self._mouse_button_sync, "left", False)
        return {
            "cancelled": bool(action_id),
            "action_id": action_id,
            "permissions_revoked": True,
        }

    @staticmethod
    def _stable_element_id(
        control_type: str,
        automation_id: str,
        name: str,
        path: str,
    ) -> str:
        if automation_id:
            identity = f"automation:{control_type.casefold()}:{automation_id.casefold()}"
        else:
            identity = f"path:{control_type.casefold()}:{name.casefold()}:{path}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return f"uia:{digest}"

    async def list_apps(self) -> dict[str, Any]:
        windows = await asyncio.to_thread(self._list_windows_sync)
        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        for window in windows:
            key = (window["pid"], window.get("executable") or window.get("process_name") or "")
            app = grouped.setdefault(key, {
                "app_id": f"process:{window.get('executable') or window.get('process_name') or window['pid']}",
                "pid": window["pid"],
                "display_name": window.get("process_name") or "Desktop app",
                "executable": window.get("executable") or "",
                "window_count": 0,
                "windows": [],
            })
            app["window_count"] += 1
            app["windows"].append({"window_id": window["window_id"], "title": window["title"]})
        return {"apps": list(grouped.values()), "count": len(grouped)}

    async def list_windows(self) -> dict[str, Any]:
        windows = await asyncio.to_thread(self._list_windows_sync)
        return {"windows": windows, "count": len(windows)}

    async def get_state(
        self,
        window_id: int | None = None,
        *,
        include_screenshot: bool = True,
        include_accessibility: bool = True,
    ) -> dict[str, Any]:
        state = await asyncio.to_thread(
            self._get_state_sync,
            window_id,
            include_screenshot,
            include_accessibility,
        )
        observed_screenshot = state.get("screenshot")
        if isinstance(observed_screenshot, dict):
            self._last_viewport = {
                key: value for key, value in observed_screenshot.items() if key != "image_data_url"
            }
            self._last_viewport_at = time.monotonic()
        snapshot_id = str(uuid4())
        elements = state.pop("_elements", [])
        self._prune_snapshots()
        self._snapshots[snapshot_id] = _Snapshot(window_id=window_id, created_at=time.monotonic(), elements=elements)
        while len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots.popitem(last=False)
        return {"snapshot_id": snapshot_id, **state}

    async def activate_window(self, window_id: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._activate_window_sync, self._coerce_window_id(window_id))

    async def click(
        self,
        window_id: int,
        *,
        snapshot_id: str | None = None,
        element_index: int | None = None,
        element_id: str | None = None,
        element_text: str | None = None,
        control_type: str | None = None,
        x: int | None = None,
        y: int | None = None,
        coordinate_space: Literal["window", "screen"] = "window",
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
        auto_retry: bool = True,
    ) -> dict[str, Any]:
        hwnd = self._coerce_window_id(window_id)
        point, resolved_snapshot_id, retried = await self._resolve_point_with_retry(
            hwnd,
            snapshot_id,
            element_index,
            x,
            y,
            coordinate_space,
            element_id=element_id,
            element_text=element_text,
            control_type=control_type,
            auto_retry=auto_retry,
        )
        try:
            result = await asyncio.to_thread(self._click_sync, hwnd, point, button, click_count)
        except ComputerUseError:
            if retried or not auto_retry or not (element_id or element_text):
                raise
            refreshed = await self.get_state(
                hwnd,
                include_screenshot=False,
                include_accessibility=True,
            )
            resolved_snapshot_id = refreshed["snapshot_id"]
            point = await asyncio.to_thread(
                self._resolve_point_sync,
                hwnd,
                resolved_snapshot_id,
                None,
                None,
                None,
                "window",
                element_id,
                element_text,
                control_type,
            )
            result = await asyncio.to_thread(self._click_sync, hwnd, point, button, click_count)
            retried = True
        return {
            **result,
            "resolved_snapshot_id": resolved_snapshot_id,
            "matched_element_id": element_id,
            "retried": retried,
        }

    async def type_text(
        self,
        window_id: int,
        text: str,
        *,
        snapshot_id: str | None = None,
        element_index: int | None = None,
        element_id: str | None = None,
        element_text: str | None = None,
        control_type: str | None = None,
        auto_retry: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(text, str) or len(text) > MAX_TEXT_INPUT:
            raise ComputerUseError(f"Text input must be at most {MAX_TEXT_INPUT} characters")
        hwnd = self._coerce_window_id(window_id)
        point = None
        resolved_snapshot_id = snapshot_id
        retried = False
        if element_index is not None or element_id or element_text:
            point, resolved_snapshot_id, retried = await self._resolve_point_with_retry(
                hwnd,
                snapshot_id,
                element_index,
                None,
                None,
                "window",
                element_id=element_id,
                element_text=element_text,
                control_type=control_type,
                auto_retry=auto_retry,
            )
        result = await asyncio.to_thread(self._type_text_sync, hwnd, text, point)
        return {
            **result,
            "resolved_snapshot_id": resolved_snapshot_id,
            "matched_element_id": element_id,
            "retried": retried,
        }

    async def press_key(self, window_id: int, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip() or len(key) > 100:
            raise ComputerUseError("Invalid key or key chord")
        return await asyncio.to_thread(self._press_key_sync, self._coerce_window_id(window_id), key)

    async def set_value(
        self,
        window_id: int,
        snapshot_id: str | None,
        element_index: int | None,
        value: str,
        *,
        element_id: str | None = None,
        element_text: str | None = None,
        control_type: str | None = None,
        auto_retry: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(value, str) or len(value) > MAX_TEXT_INPUT:
            raise ComputerUseError(f"Value must be at most {MAX_TEXT_INPUT} characters")
        hwnd = self._coerce_window_id(window_id)
        point, resolved_snapshot_id, retried = await self._resolve_point_with_retry(
            hwnd,
            snapshot_id,
            element_index,
            None,
            None,
            "window",
            element_id=element_id,
            element_text=element_text,
            control_type=control_type,
            auto_retry=auto_retry,
        )
        result = await asyncio.to_thread(self._set_value_sync, hwnd, point, value)
        return {
            **result,
            "resolved_snapshot_id": resolved_snapshot_id,
            "matched_element_id": element_id,
            "retried": retried,
        }

    async def scroll(
        self,
        window_id: int,
        *,
        scroll_y: int,
        scroll_x: int = 0,
        x: int | None = None,
        y: int | None = None,
        coordinate_space: Literal["window", "screen"] = "window",
    ) -> dict[str, Any]:
        hwnd = self._coerce_window_id(window_id)
        point = await asyncio.to_thread(
            self._resolve_point_sync,
            hwnd,
            None,
            None,
            x,
            y,
            coordinate_space,
        )
        return await asyncio.to_thread(self._scroll_sync, hwnd, point, scroll_x, scroll_y)

    async def drag(
        self,
        window_id: int,
        *,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        coordinate_space: Literal["window", "screen"] = "window",
        duration_ms: int = 500,
    ) -> dict[str, Any]:
        hwnd = self._coerce_window_id(window_id)
        start = await asyncio.to_thread(
            self._resolve_point_sync, hwnd, None, None, from_x, from_y, coordinate_space
        )
        end = await asyncio.to_thread(
            self._resolve_point_sync, hwnd, None, None, to_x, to_y, coordinate_space
        )
        return await asyncio.to_thread(self._drag_sync, hwnd, start, end, duration_ms)

    async def launch_app(self, executable: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._launch_app_sync, executable)

    async def list_directory(self, path_value: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._list_directory_sync, path_value)

    @staticmethod
    def _coerce_window_id(value: Any) -> int:
        try:
            hwnd = int(value)
        except (TypeError, ValueError) as exc:
            raise ComputerUseError("window_id must be an integer") from exc
        if os.name != "nt":
            from . import x11_desktop

            if hwnd <= 0 or not x11_desktop.window_exists(hwnd):
                raise ComputerUseError("The selected X11 window no longer exists")
        else:
            try:
                import win32gui
            except ImportError as exc:
                raise ComputerUseError("Windows desktop dependencies are unavailable") from exc
            if hwnd <= 0 or not win32gui.IsWindow(hwnd):
                raise ComputerUseError("The selected window no longer exists")
        return hwnd

    @staticmethod
    def _list_windows_sync() -> list[dict[str, Any]]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                return x11_desktop.list_windows()
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        import psutil
        import win32gui
        import win32process

        windows: list[dict[str, Any]] = []

        def collect(hwnd: int, _extra: Any) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left < 2 or bottom - top < 2:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = ""
            executable = ""
            try:
                process = psutil.Process(pid)
                process_name = process.name()
                executable = process.exe()
            except (psutil.Error, OSError):
                pass
            windows.append({
                "window_id": int(hwnd),
                "title": title[:300],
                "pid": int(pid),
                "process_name": process_name,
                "executable": executable,
                "bounds": {"left": left, "top": top, "width": right - left, "height": bottom - top},
            })

        win32gui.EnumWindows(collect, None)
        return windows[:200]

    def _get_state_sync(
        self,
        window_id: int | None,
        include_screenshot: bool,
        include_accessibility: bool,
    ) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            if window_id is None:
                bounds = x11_desktop.screen_bounds()
                left, top = bounds["left"], bounds["top"]
                width, height = bounds["width"], bounds["height"]
                window = None
                windows = self._list_windows_sync()
                elements = [
                    {
                        "index": index,
                        "element_id": f"window:{item['window_id']}",
                        "name": item["title"],
                        "control_type": "Window",
                        "automation_id": "",
                        "enabled": True,
                        "bounds": item["bounds"],
                        "window_id": item["window_id"],
                    }
                    for index, item in enumerate(windows)
                ]
                tree = "\n".join(
                    f'{item["index"]} [{item["element_id"]}] Window: {item["name"]!r} '
                    f'(window_id={item["window_id"]})'
                    for item in elements
                )
            else:
                x11_window_id = self._coerce_window_id(window_id)
                bounds = x11_desktop.window_bounds(x11_window_id)
                left, top = bounds["left"], bounds["top"]
                width, height = bounds["width"], bounds["height"]
                window = {
                    "window_id": x11_window_id,
                    "title": x11_desktop.window_title(x11_window_id),
                    "bounds": bounds,
                }
                # Screenshot-first Computer Use does not require an
                # accessibility tree.  Keep a stable Window node so callers
                # can still target and reactivate the observed surface.
                elements = [{
                    "index": 0,
                    "element_id": f"window:{x11_window_id}",
                    "name": window["title"],
                    "control_type": "Window",
                    "automation_id": "",
                    "enabled": True,
                    "bounds": bounds,
                    "window_id": x11_window_id,
                }] if include_accessibility else []
                tree = (
                    f'0 [window:{x11_window_id}] Window: {window["title"]!r}'
                    if include_accessibility else ""
                )
            x11_result: dict[str, Any] = {
                "window": window,
                "accessibility_tree": tree,
                "elements": elements,
                "captured_at": time.time(),
                "_elements": elements,
            }
            if include_screenshot:
                x11_result["screenshot"] = self._capture_region_sync(left, top, width, height)
            return x11_result
        import win32api
        import win32con
        import win32gui

        if window_id is None:
            left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            window = None
            windows = self._list_windows_sync()
            elements = [
                {
                    "index": index,
                    "element_id": f"window:{item['window_id']}",
                    "name": item["title"],
                    "control_type": "Window",
                    "automation_id": "",
                    "enabled": True,
                    "bounds": item["bounds"],
                    "window_id": item["window_id"],
                }
                for index, item in enumerate(windows)
            ]
            tree = "\n".join(
                (
                    f'{item["index"]} [{item["element_id"]}] Window: '
                    f'{item["name"]!r} (window_id={item["window_id"]})'
                )
                for item in elements
            )
        else:
            hwnd = self._coerce_window_id(window_id)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width < 2 or height < 2:
                raise ComputerUseError("The selected window has no capturable area")
            window = {
                "window_id": hwnd,
                "title": win32gui.GetWindowText(hwnd)[:300],
                "bounds": {"left": left, "top": top, "width": width, "height": height},
            }
            elements, tree = self._accessibility_tree_sync(hwnd) if include_accessibility else ([], "")

        result: dict[str, Any] = {
            "window": window,
            "accessibility_tree": tree,
            "elements": elements,
            "captured_at": time.time(),
            "_elements": elements,
        }
        if include_screenshot:
            if window_id is None:
                result["screenshot"] = self._capture_region_sync(left, top, width, height)
            else:
                try:
                    result["screenshot"] = self._capture_window_sync(window_id, left, top, width, height)
                except Exception:
                    result["screenshot"] = self._capture_region_sync(left, top, width, height)
        return result

    @staticmethod
    def _accessibility_tree_sync(hwnd: int) -> tuple[list[dict[str, Any]], str]:
        import pythoncom
        from pywinauto import Desktop

        elements: list[dict[str, Any]] = []
        lines: list[str] = []
        pythoncom.CoInitialize()
        try:
            root = Desktop(backend="uia").window(handle=hwnd).wrapper_object()

            def walk(control: Any, depth: int, path: str) -> None:
                if len(elements) >= MAX_ACCESSIBILITY_ELEMENTS or depth > 14:
                    return
                try:
                    info = control.element_info
                    name = (info.name or "").replace("\r", " ").replace("\n", " ")[:240]
                    control_type = info.control_type or control.friendly_class_name() or "Control"
                    rectangle = info.rectangle
                    bounds = {
                        "left": int(rectangle.left),
                        "top": int(rectangle.top),
                        "width": max(0, int(rectangle.right - rectangle.left)),
                        "height": max(0, int(rectangle.bottom - rectangle.top)),
                    }
                    index = len(elements)
                    automation_id = str(info.automation_id or "")[:200]
                    element_id = ComputerUseService._stable_element_id(
                        str(control_type),
                        automation_id,
                        name,
                        path,
                    )
                    element = {
                        "index": index,
                        "element_id": element_id,
                        "name": name,
                        "control_type": control_type,
                        "automation_id": automation_id,
                        "selector": {
                            "element_id": element_id,
                            "automation_id": automation_id,
                            "control_type": str(control_type),
                            "text": name,
                        },
                        "enabled": bool(info.enabled),
                        "bounds": bounds,
                    }
                    value = ""
                    if control_type in {"Edit", "Document"}:
                        for getter_name in ("get_value", "window_text"):
                            try:
                                getter = getattr(control, getter_name)
                                candidate = getter()
                                if candidate:
                                    value = str(candidate).replace("\r", " ").replace("\n", " ")[:1000]
                                    break
                            except Exception:
                                continue
                    if value and value != name:
                        element["value"] = value
                    elements.append(element)
                    label = f"{index} [{element_id}] {control_type}"
                    if name:
                        label += f" {name}"
                    if not element["enabled"]:
                        label += " (disabled)"
                    if value and value != name:
                        label += f" Value: {value}"
                    lines.append("\t" * depth + label)
                    for child_index, child in enumerate(control.children()):
                        if len(elements) >= MAX_ACCESSIBILITY_ELEMENTS:
                            break
                        walk(child, depth + 1, f"{path}/{child_index}")
                except Exception:
                    return

            walk(root, 0, "0")
        except Exception as exc:
            lines.append(f"Accessibility tree unavailable: {exc}")
        finally:
            pythoncom.CoUninitialize()
        return elements, "\n".join(lines)

    @staticmethod
    def _capture_region_sync(left: int, top: int, width: int, height: int) -> dict[str, Any]:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
        return ComputerUseService._encode_screenshot(image, left, top)

    @staticmethod
    def _capture_window_sync(hwnd: int, left: int, top: int, width: int, height: int) -> dict[str, Any]:
        import win32gui
        import win32ui
        from PIL import Image

        window_dc = win32gui.GetWindowDC(hwnd)
        source_dc = win32ui.CreateDCFromHandle(window_dc)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)
        try:
            printed = ctypes.windll.user32.PrintWindow(  # type: ignore[attr-defined]
                hwnd,
                memory_dc.GetSafeHdc(),
                2,
            )
            if not printed:
                raise ComputerUseError("PrintWindow did not return an image")
            info = bitmap.GetInfo()
            bits = bitmap.GetBitmapBits(True)
            image = Image.frombuffer(
                "RGB",
                (info["bmWidth"], info["bmHeight"]),
                bits,
                "raw",
                "BGRX",
                0,
                1,
            )
            if not image.getbbox():
                raise ComputerUseError("PrintWindow returned an empty image")
            return ComputerUseService._encode_screenshot(image, left, top)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            memory_dc.DeleteDC()
            source_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, window_dc)

    @staticmethod
    def _encode_screenshot(image: Any, left: int, top: int) -> dict[str, Any]:
        from PIL import Image

        native_width, native_height = image.size
        if native_width < 1 or native_height < 1:
            raise ComputerUseError("Screen capture returned an empty image")
        # Keep the pixels and the coordinate system in lockstep.  This follows
        # Anthropic's conservative 1.15 MP / 1568 px guidance and returns the
        # exact inverse scale used for later clicks.
        long_edge_scale = 1568 / max(native_width, native_height)
        pixel_scale = math.sqrt(1_150_000 / (native_width * native_height))
        scale = min(1.0, long_edge_scale, pixel_scale)
        encoded_image = image
        if scale < 1.0:
            encoded_image = image.resize(
                (
                    max(1, round(native_width * scale)),
                    max(1, round(native_height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        encoded_image.save(buffer, format="PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {
            "image_data_url": f"data:image/png;base64,{encoded}",
            "width": encoded_image.width,
            "height": encoded_image.height,
            "display_width_px": encoded_image.width,
            "display_height_px": encoded_image.height,
            "native_width": native_width,
            "native_height": native_height,
            "origin_x": left,
            "origin_y": top,
            "scale_x": native_width / encoded_image.width,
            "scale_y": native_height / encoded_image.height,
        }

    def _snapshot_element(
        self,
        snapshot_id: str | None,
        window_id: int,
        element_index: int | None,
        element_id: str | None = None,
        element_text: str | None = None,
        control_type: str | None = None,
    ) -> dict[str, Any]:
        self._prune_snapshots()
        if not snapshot_id or snapshot_id not in self._snapshots:
            raise ComputerUseError("snapshot_id is missing or expired; observe the window again")
        snapshot = self._snapshots[snapshot_id]
        if snapshot.window_id != window_id:
            raise ComputerUseError("The snapshot belongs to a different window")
        if element_id:
            matched = next(
                (
                    element for element in snapshot.elements
                    if element.get("element_id") == element_id
                ),
                None,
            )
            if matched is None:
                raise ComputerUseError(
                    f"element_id {element_id!r} is not present in this snapshot"
                )
            return matched
        if element_index is not None:
            if not isinstance(element_index, int) or isinstance(element_index, bool):
                raise ComputerUseError("element_index must be an integer")
            if element_index < 0 or element_index >= len(snapshot.elements):
                raise ComputerUseError("element_index is outside the snapshot")
            return snapshot.elements[element_index]
        needle = " ".join(str(element_text or "").casefold().split())
        expected_type = str(control_type or "").casefold().strip()
        if not needle and not expected_type:
            raise ComputerUseError(
                "Select an element with element_id, element_index, element_text, or control_type"
            )
        ranked: list[tuple[float, dict[str, Any]]] = []
        for element in snapshot.elements:
            actual_type = str(element.get("control_type") or "").casefold()
            if expected_type and actual_type != expected_type:
                continue
            haystack = " ".join(str(element.get("name") or "").casefold().split())
            if not needle:
                score = 1.0
            elif haystack == needle:
                score = 3.0
            elif needle in haystack:
                score = 2.0 + len(needle) / max(1, len(haystack))
            else:
                score = SequenceMatcher(None, needle, haystack).ratio()
            if score >= 0.55:
                ranked.append((score, element))
        if not ranked:
            description = element_text or control_type or "selector"
            raise ComputerUseError(f"No element fuzzily matched {description!r}")
        ranked.sort(
            key=lambda item: (
                item[0],
                bool(item[1].get("enabled")),
                -int(item[1].get("index", 0)),
            ),
            reverse=True,
        )
        return ranked[0][1]

    def _prune_snapshots(self) -> None:
        cutoff = time.monotonic() - SNAPSHOT_TTL_SECONDS
        stale = [key for key, value in self._snapshots.items() if value.created_at < cutoff]
        for key in stale:
            self._snapshots.pop(key, None)

    async def _resolve_point_with_retry(
        self,
        hwnd: int,
        snapshot_id: str | None,
        element_index: int | None,
        x: int | None,
        y: int | None,
        coordinate_space: str,
        *,
        element_id: str | None,
        element_text: str | None,
        control_type: str | None,
        auto_retry: bool,
    ) -> tuple[tuple[int, int], str | None, bool]:
        try:
            point = await asyncio.to_thread(
                self._resolve_point_sync,
                hwnd,
                snapshot_id,
                element_index,
                x,
                y,
                coordinate_space,
                element_id,
                element_text,
                control_type,
            )
            return point, snapshot_id, False
        except ComputerUseError:
            if not auto_retry or not (element_id or element_text or control_type):
                raise
        refreshed = await self.get_state(
            hwnd,
            include_screenshot=False,
            include_accessibility=True,
        )
        refreshed_snapshot_id = str(refreshed["snapshot_id"])
        point = await asyncio.to_thread(
            self._resolve_point_sync,
            hwnd,
            refreshed_snapshot_id,
            None,
            x,
            y,
            coordinate_space,
            element_id,
            element_text,
            control_type,
        )
        return point, refreshed_snapshot_id, True

    def _resolve_point_sync(
        self,
        hwnd: int,
        snapshot_id: str | None,
        element_index: int | None,
        x: int | None,
        y: int | None,
        coordinate_space: str,
        element_id: str | None = None,
        element_text: str | None = None,
        control_type: str | None = None,
    ) -> tuple[int, int]:
        if os.name == "nt":
            import win32api
            import win32con
            import win32gui
            window_bounds = None
        else:
            from . import x11_desktop

            window_bounds = x11_desktop.window_bounds(hwnd)

        if element_index is not None or element_id or element_text or control_type:
            element = self._snapshot_element(
                snapshot_id,
                hwnd,
                element_index,
                element_id,
                element_text,
                control_type,
            )
            bounds = element["bounds"]
            if bounds["width"] < 1 or bounds["height"] < 1:
                raise ComputerUseError("The selected element has no clickable bounds")
            screen_x = bounds["left"] + bounds["width"] // 2
            screen_y = bounds["top"] + bounds["height"] // 2
        elif x is None or y is None:
            if os.name == "nt":
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                screen_x = left + max(1, (right - left) // 2)
                screen_y = top + max(1, (bottom - top) // 2)
            else:
                assert window_bounds is not None
                screen_x = window_bounds["left"] + max(1, window_bounds["width"] // 2)
                screen_y = window_bounds["top"] + max(1, window_bounds["height"] // 2)
        else:
            try:
                screen_x, screen_y = int(x), int(y)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("Coordinates must be integers") from exc
            if coordinate_space == "window":
                if os.name == "nt":
                    left, top, _, _ = win32gui.GetWindowRect(hwnd)
                    screen_x += left
                    screen_y += top
                else:
                    assert window_bounds is not None
                    screen_x += window_bounds["left"]
                    screen_y += window_bounds["top"]
            elif coordinate_space != "screen":
                raise ComputerUseError("coordinate_space must be window or screen")

        if os.name == "nt":
            virtual_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            virtual_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            virtual_right = virtual_left + win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            virtual_bottom = virtual_top + win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        else:
            desktop_bounds = x11_desktop.screen_bounds()
            virtual_left = desktop_bounds["left"]
            virtual_top = desktop_bounds["top"]
            virtual_right = virtual_left + desktop_bounds["width"]
            virtual_bottom = virtual_top + desktop_bounds["height"]
        if not (virtual_left <= screen_x < virtual_right and virtual_top <= screen_y < virtual_bottom):
            raise ComputerUseError("Target coordinates are outside the virtual desktop")
        return screen_x, screen_y

    @staticmethod
    def _activate_window_sync(hwnd: int) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            try:
                result = x11_desktop.activate_window(hwnd)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
            if result.get("activated") is not True:
                raise ComputerUseError("The X11 window manager did not grant focus to the selected window")
            return result
        import win32api
        import win32con
        import win32gui

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            # A harmless Alt press lets the current foreground process hand
            # focus to the explicitly selected window under Windows' focus rules.
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            try:
                win32gui.BringWindowToTop(hwnd)
            except Exception as exc:
                raise ComputerUseError(f"Unable to activate the selected window: {exc}") from exc
        time.sleep(0.08)
        if win32gui.GetForegroundWindow() != hwnd:
            raise ComputerUseError("Windows did not grant foreground focus to the selected window")
        return {"activated": True, "window_id": hwnd, "title": win32gui.GetWindowText(hwnd)[:300]}

    @classmethod
    def _click_sync(
        cls,
        hwnd: int,
        point: tuple[int, int],
        button: str,
        click_count: int,
    ) -> dict[str, Any]:
        if button not in {"left", "right", "middle"}:
            raise ComputerUseError("Unsupported mouse button")
        if not isinstance(click_count, int) or isinstance(click_count, bool) or not 1 <= click_count <= 3:
            raise ComputerUseError("click_count must be between 1 and 3")
        cls._activate_window_sync(hwnd)
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.move_cursor(*point)
                x11_desktop.click(button, click_count)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
            return {"clicked": True, "window_id": hwnd, "x": point[0], "y": point[1], "button": button, "click_count": click_count}
        import win32api
        import win32con

        win32api.SetCursorPos(point)
        flags = {
            "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
            "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
            "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
        }[button]
        for _ in range(click_count):
            win32api.mouse_event(flags[0], 0, 0, 0, 0)
            win32api.mouse_event(flags[1], 0, 0, 0, 0)
            if click_count > 1:
                time.sleep(0.08)
        return {"clicked": True, "window_id": hwnd, "x": point[0], "y": point[1], "button": button, "click_count": click_count}

    @classmethod
    def _type_text_sync(cls, hwnd: int, text: str, point: tuple[int, int] | None) -> dict[str, Any]:
        cls._activate_window_sync(hwnd)
        if point is not None:
            cls._click_sync(hwnd, point, "left", 1)
        cls._send_unicode_text(text)
        return {"typed": True, "window_id": hwnd, "character_count": len(text)}

    @classmethod
    def _set_value_sync(cls, hwnd: int, point: tuple[int, int], value: str) -> dict[str, Any]:
        cls._click_sync(hwnd, point, "left", 1)
        cls._press_key_sync(hwnd, "Control_L+a")
        cls._send_unicode_text(value)
        return {"value_set": True, "window_id": hwnd, "character_count": len(value)}

    @staticmethod
    def _send_unicode_text(text: str) -> None:
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.type_text(text)
                return
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
        from ctypes import wintypes

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM),
            ]

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM),
            ]

        class HardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

        key_event_unicode = 0x0004
        key_event_up = 0x0002
        send_input = ctypes.windll.user32.SendInput  # type: ignore[attr-defined]
        send_input.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        send_input.restype = wintypes.UINT
        encoded = text.encode("utf-16-le")
        for offset in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[offset:offset + 2], "little")
            events = (Input * 2)(
                Input(type=1, ki=KeyboardInput(0, unit, key_event_unicode, 0, 0)),
                Input(type=1, ki=KeyboardInput(0, unit, key_event_unicode | key_event_up, 0, 0)),
            )
            if send_input(2, events, ctypes.sizeof(Input)) != 2:
                raise ComputerUseError("Windows rejected Unicode keyboard input")

    @classmethod
    def _press_key_sync(cls, hwnd: int, key: str) -> dict[str, Any]:
        if os.name != "nt":
            from . import x11_desktop

            cls._activate_window_sync(hwnd)
            try:
                x11_desktop.press_key(key)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
            return {"pressed": True, "window_id": hwnd, "key": key}
        import win32api
        import win32con

        cls._activate_window_sync(hwnd)
        aliases = {
            "ctrl": win32con.VK_CONTROL, "control": win32con.VK_CONTROL, "control_l": win32con.VK_CONTROL,
            "shift": win32con.VK_SHIFT, "shift_l": win32con.VK_SHIFT,
            "alt": win32con.VK_MENU, "alt_l": win32con.VK_MENU,
            "win": win32con.VK_LWIN, "super": win32con.VK_LWIN,
        }
        named = {
            "return": win32con.VK_RETURN, "enter": win32con.VK_RETURN, "tab": win32con.VK_TAB,
            "escape": win32con.VK_ESCAPE, "esc": win32con.VK_ESCAPE, "space": win32con.VK_SPACE,
            "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE, "home": win32con.VK_HOME,
            "end": win32con.VK_END, "pageup": win32con.VK_PRIOR, "pagedown": win32con.VK_NEXT,
            "up": win32con.VK_UP, "down": win32con.VK_DOWN, "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
        }
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if not parts:
            raise ComputerUseError("Invalid key chord")
        modifier_codes = [aliases[part] for part in parts[:-1] if part in aliases]
        if len(modifier_codes) != len(parts) - 1:
            raise ComputerUseError("Only Ctrl, Shift, Alt and Win modifiers are supported")
        final = parts[-1]
        if final in named:
            key_code = named[final]
        elif final.startswith("f") and final[1:].isdigit() and 1 <= int(final[1:]) <= 24:
            key_code = win32con.VK_F1 + int(final[1:]) - 1
        elif len(final) == 1:
            key_code = ord(final.upper())
        else:
            raise ComputerUseError(f"Unsupported key: {final}")
        for code in modifier_codes:
            win32api.keybd_event(code, 0, 0, 0)
        win32api.keybd_event(key_code, 0, 0, 0)
        win32api.keybd_event(key_code, 0, win32con.KEYEVENTF_KEYUP, 0)
        for code in reversed(modifier_codes):
            win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)
        return {"pressed": True, "window_id": hwnd, "key": key}

    @classmethod
    def _scroll_sync(
        cls,
        hwnd: int,
        point: tuple[int, int],
        scroll_x: int,
        scroll_y: int,
    ) -> dict[str, Any]:
        cls._activate_window_sync(hwnd)
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.move_cursor(*point)
                result = x11_desktop.scroll(scroll_x * 100, scroll_y * 100)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
            return {"scrolled": True, "window_id": hwnd, **result, "x": point[0], "y": point[1]}
        import win32api
        import win32con

        win32api.SetCursorPos(point)
        vertical = max(-50, min(50, int(scroll_y)))
        horizontal = max(-50, min(50, int(scroll_x)))
        if vertical:
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -vertical * win32con.WHEEL_DELTA, 0)
        if horizontal and hasattr(win32con, "MOUSEEVENTF_HWHEEL"):
            win32api.mouse_event(win32con.MOUSEEVENTF_HWHEEL, 0, 0, horizontal * win32con.WHEEL_DELTA, 0)
        return {"scrolled": True, "window_id": hwnd, "scroll_x": horizontal, "scroll_y": vertical, "x": point[0], "y": point[1]}

    @classmethod
    def _drag_sync(
        cls,
        hwnd: int,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int,
    ) -> dict[str, Any]:
        cls._activate_window_sync(hwnd)
        if os.name != "nt":
            from . import x11_desktop

            try:
                x11_desktop.drag(start, end, duration_ms)
            except x11_desktop.X11DesktopError as exc:
                raise ComputerUseError(str(exc)) from exc
            return {"dragged": True, "window_id": hwnd, "from": list(start), "to": list(end), "duration_ms": max(100, min(5000, int(duration_ms)))}
        import win32api
        import win32con

        duration = max(100, min(5000, int(duration_ms))) / 1000
        steps = max(5, min(100, int(duration * 60)))
        win32api.SetCursorPos(start)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        try:
            for step in range(1, steps + 1):
                ratio = step / steps
                point = (
                    round(start[0] + (end[0] - start[0]) * ratio),
                    round(start[1] + (end[1] - start[1]) * ratio),
                )
                win32api.SetCursorPos(point)
                time.sleep(duration / steps)
        finally:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return {"dragged": True, "window_id": hwnd, "from": list(start), "to": list(end), "duration_ms": round(duration * 1000)}

    @staticmethod
    def _launch_app_sync(executable: str) -> dict[str, Any]:
        if not isinstance(executable, str) or not executable.strip() or "\x00" in executable:
            raise ComputerUseError("Invalid executable path")
        if os.name != "nt":
            raw = executable.strip()
            if any(character.isspace() for character in raw):
                raise ComputerUseError("Application launch accepts one executable path without arguments")
            candidate = Path(raw).expanduser()
            resolved_value = str(candidate.resolve(strict=True)) if candidate.is_absolute() else shutil.which(raw)
            if not resolved_value:
                raise ComputerUseError("The requested executable is not installed")
            path = Path(resolved_value).resolve(strict=True)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ComputerUseError("The requested application is not executable")
            process = subprocess.Popen(
                [str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"launched": True, "display_name": path.name, "pid": process.pid}
        path = Path(executable).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in {".exe", ".lnk"}:
            raise ComputerUseError("Only an existing .exe or .lnk may be launched")
        os.startfile(str(path))  # type: ignore[attr-defined]
        return {"launched": True, "display_name": path.name}

    @staticmethod
    def _list_directory_sync(path_value: str) -> dict[str, Any]:
        if not isinstance(path_value, str) or not path_value.strip() or "\x00" in path_value:
            raise ComputerUseError("Invalid directory path")
        raw = path_value.strip()
        if raw.startswith(("\\\\", "//")):
            raise ComputerUseError("Network and device paths are not supported")
        path = Path(raw).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ComputerUseError("The selected path is not a directory")
        items = []
        for child in sorted(path.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
            try:
                stat = child.stat()
                items.append({
                    "name": child.name,
                    "kind": "directory" if child.is_dir() else "file",
                    "size_bytes": None if child.is_dir() else stat.st_size,
                    "modified_at": stat.st_mtime,
                })
            except OSError:
                continue
            if len(items) >= MAX_DIRECTORY_ITEMS:
                break
        return {"display_name": path.name or str(path), "items": items, "count": len(items), "truncated": len(items) >= MAX_DIRECTORY_ITEMS}


computer_use_service = ComputerUseService()
