from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import io
import os
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

MAX_ACCESSIBILITY_ELEMENTS = 300
MAX_SNAPSHOTS = 64
MAX_TEXT_INPUT = 20_000
MAX_DIRECTORY_ITEMS = 500
SNAPSHOT_TTL_SECONDS = 15 * 60


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
    """Windows-local observation/action runtime used by the trusted built-in plugin.

    The service never decides whether an action is allowed. The plugin dispatcher
    obtains approval from Electron's private consent broker before calling here.
    """

    def __init__(self) -> None:
        self._snapshots: OrderedDict[str, _Snapshot] = OrderedDict()

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
                "display_name": window.get("process_name") or "Windows app",
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
            raise ComputerUseError("Computer Use is supported only on Windows")
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
            raise ComputerUseError("Computer Use is supported only on Windows")
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
        image.thumbnail((1280, 900))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {
            "image_data_url": f"data:image/png;base64,{encoded}",
            "width": image.width,
            "height": image.height,
            "origin_x": left,
            "origin_y": top,
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
        import win32api
        import win32con
        import win32gui

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
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            screen_x = left + max(1, (right - left) // 2)
            screen_y = top + max(1, (bottom - top) // 2)
        else:
            try:
                screen_x, screen_y = int(x), int(y)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("Coordinates must be integers") from exc
            if coordinate_space == "window":
                left, top, _, _ = win32gui.GetWindowRect(hwnd)
                screen_x += left
                screen_y += top
            elif coordinate_space != "screen":
                raise ComputerUseError("coordinate_space must be window or screen")

        virtual_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        virtual_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        virtual_right = virtual_left + win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        virtual_bottom = virtual_top + win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        if not (virtual_left <= screen_x < virtual_right and virtual_top <= screen_y < virtual_bottom):
            raise ComputerUseError("Target coordinates are outside the virtual desktop")
        return screen_x, screen_y

    @staticmethod
    def _activate_window_sync(hwnd: int) -> dict[str, Any]:
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
        import win32api
        import win32con

        if button not in {"left", "right", "middle"}:
            raise ComputerUseError("Unsupported mouse button")
        if not isinstance(click_count, int) or isinstance(click_count, bool) or not 1 <= click_count <= 3:
            raise ComputerUseError("click_count must be between 1 and 3")
        cls._activate_window_sync(hwnd)
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
        import win32api
        import win32con

        cls._activate_window_sync(hwnd)
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
        import win32api
        import win32con

        cls._activate_window_sync(hwnd)
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
