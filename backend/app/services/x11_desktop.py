"""Small, dependency-free X11/XTest desktop automation adapter.

The packaged Linux backend embeds Python, so relying on a system Python module
such as python-xlib makes Computer Use disappear after packaging.  This module
talks to the X11 and XTest shared libraries directly.  It deliberately exposes
only the narrow operations required by the trusted desktop-control plugin.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Lock
from typing import Any


class X11DesktopError(RuntimeError):
    pass


Window = ctypes.c_ulong
Atom = ctypes.c_ulong
Bool = ctypes.c_int


class _ClientMessageData(ctypes.Union):
    _fields_ = [
        ("b", ctypes.c_char * 20),
        ("s", ctypes.c_short * 10),
        ("l", ctypes.c_long * 5),
    ]


class _ClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", Bool),
        ("display", ctypes.c_void_p),
        ("window", Window),
        ("message_type", Atom),
        ("format", ctypes.c_int),
        ("data", _ClientMessageData),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xclient", _ClientMessageEvent),
        ("padding", ctypes.c_long * 24),
    ]


_LIBRARY_LOCK = Lock()
_X11: ctypes.CDLL | None = None
_XTST: ctypes.CDLL | None = None


def _configure_libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    global _X11, _XTST
    with _LIBRARY_LOCK:
        if _X11 is not None and _XTST is not None:
            return _X11, _XTST
        x11_path = ctypes.util.find_library("X11")
        xtst_path = ctypes.util.find_library("Xtst")
        if not x11_path or not xtst_path:
            raise X11DesktopError("X11/XTest runtime libraries are unavailable")
        x11 = ctypes.CDLL(x11_path)
        xtst = ctypes.CDLL(xtst_path)

        x11.XInitThreads.argtypes = []
        x11.XInitThreads.restype = ctypes.c_int
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = Window
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, Bool]
        x11.XInternAtom.restype = Atom
        x11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            Window,
            Atom,
            ctypes.c_long,
            ctypes.c_long,
            Bool,
            Atom,
            ctypes.POINTER(Atom),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        x11.XGetWindowProperty.restype = ctypes.c_int
        x11.XFree.argtypes = [ctypes.c_void_p]
        x11.XFree.restype = ctypes.c_int
        x11.XGetGeometry.argtypes = [
            ctypes.c_void_p,
            Window,
            ctypes.POINTER(Window),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        x11.XGetGeometry.restype = Bool
        x11.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p,
            Window,
            Window,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(Window),
        ]
        x11.XTranslateCoordinates.restype = Bool
        x11.XQueryPointer.argtypes = [
            ctypes.c_void_p,
            Window,
            ctypes.POINTER(Window),
            ctypes.POINTER(Window),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        x11.XQueryPointer.restype = Bool
        x11.XSendEvent.argtypes = [
            ctypes.c_void_p,
            Window,
            Bool,
            ctypes.c_long,
            ctypes.POINTER(_XEvent),
        ]
        x11.XSendEvent.restype = ctypes.c_int
        x11.XMapRaised.argtypes = [ctypes.c_void_p, Window]
        x11.XMapRaised.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        x11.XSync.argtypes = [ctypes.c_void_p, Bool]
        x11.XSync.restype = ctypes.c_int
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
        x11.XKeycodeToKeysym.restype = ctypes.c_ulong

        xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        xtst.XTestFakeMotionEvent.restype = Bool
        xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            Bool,
            ctypes.c_ulong,
        ]
        xtst.XTestFakeButtonEvent.restype = Bool
        xtst.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            Bool,
            ctypes.c_ulong,
        ]
        xtst.XTestFakeKeyEvent.restype = Bool

        x11.XInitThreads()
        _X11, _XTST = x11, xtst
        return x11, xtst


@contextmanager
def _display() -> Iterator[tuple[ctypes.CDLL, ctypes.CDLL, ctypes.c_void_p, int]]:
    if not os.environ.get("DISPLAY"):
        raise X11DesktopError("DISPLAY is not set; X11 desktop control is unavailable")
    x11, xtst = _configure_libraries()
    handle = x11.XOpenDisplay(None)
    if not handle:
        raise X11DesktopError(f"Unable to open X11 display {os.environ.get('DISPLAY')}")
    root = int(x11.XDefaultRootWindow(handle))
    try:
        yield x11, xtst, handle, root
    finally:
        x11.XCloseDisplay(handle)


def available() -> bool:
    try:
        with _display():
            return True
    except (OSError, X11DesktopError):
        return False


def _atom(x11: ctypes.CDLL, display: ctypes.c_void_p, name: str) -> int:
    return int(x11.XInternAtom(display, name.encode("ascii"), False))


def _property(
    x11: ctypes.CDLL,
    display: ctypes.c_void_p,
    window: int,
    name: str,
) -> tuple[int, int, bytes | list[int]] | None:
    property_atom = _atom(x11, display, name)
    actual_type = Atom()
    actual_format = ctypes.c_int()
    count = ctypes.c_ulong()
    remaining = ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    status = x11.XGetWindowProperty(
        display,
        Window(window),
        Atom(property_atom),
        0,
        16_384,
        False,
        Atom(0),
        ctypes.byref(actual_type),
        ctypes.byref(actual_format),
        ctypes.byref(count),
        ctypes.byref(remaining),
        ctypes.byref(data),
    )
    if status != 0 or not data or count.value == 0:
        if data:
            x11.XFree(data)
        return None
    try:
        if actual_format.value == 8:
            value: bytes | list[int] = ctypes.string_at(data, count.value)
        elif actual_format.value == 32:
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            value = [int(values[index]) for index in range(count.value)]
        else:
            return None
        return int(actual_type.value), int(actual_format.value), value
    finally:
        x11.XFree(data)


def _text_property(
    x11: ctypes.CDLL,
    display: ctypes.c_void_p,
    window: int,
    *names: str,
) -> str:
    for name in names:
        result = _property(x11, display, window, name)
        if result is None or not isinstance(result[2], bytes):
            continue
        decoded = result[2].rstrip(b"\x00").decode("utf-8", errors="replace").strip()
        if decoded:
            return decoded
    return ""


def _window_bounds(
    x11: ctypes.CDLL,
    display: ctypes.c_void_p,
    root: int,
    window: int,
) -> dict[str, int]:
    root_return = Window()
    x = ctypes.c_int()
    y = ctypes.c_int()
    width = ctypes.c_uint()
    height = ctypes.c_uint()
    border = ctypes.c_uint()
    depth = ctypes.c_uint()
    if not x11.XGetGeometry(
        display,
        Window(window),
        ctypes.byref(root_return),
        ctypes.byref(x),
        ctypes.byref(y),
        ctypes.byref(width),
        ctypes.byref(height),
        ctypes.byref(border),
        ctypes.byref(depth),
    ):
        raise X11DesktopError("The selected X11 window no longer exists")
    translated_x = ctypes.c_int()
    translated_y = ctypes.c_int()
    child = Window()
    if x11.XTranslateCoordinates(
        display,
        Window(window),
        Window(root),
        0,
        0,
        ctypes.byref(translated_x),
        ctypes.byref(translated_y),
        ctypes.byref(child),
    ):
        left, top = translated_x.value, translated_y.value
    else:
        left, top = x.value, y.value
    return {
        "left": int(left),
        "top": int(top),
        "width": int(width.value),
        "height": int(height.value),
    }


def screen_bounds() -> dict[str, int]:
    with _display() as (x11, _xtst, display, root):
        bounds = _window_bounds(x11, display, root, root)
    return {"left": 0, "top": 0, "width": bounds["width"], "height": bounds["height"]}


def window_exists(window_id: int) -> bool:
    try:
        with _display() as (x11, _xtst, display, root):
            bounds = _window_bounds(x11, display, root, int(window_id))
        return bounds["width"] > 0 and bounds["height"] > 0
    except (OSError, X11DesktopError):
        return False


def list_windows() -> list[dict[str, Any]]:
    with _display() as (x11, _xtst, display, root):
        clients = _property(x11, display, root, "_NET_CLIENT_LIST_STACKING")
        if clients is None:
            clients = _property(x11, display, root, "_NET_CLIENT_LIST")
        window_ids = clients[2] if clients and isinstance(clients[2], list) else []
        windows: list[dict[str, Any]] = []
        for raw_window_id in reversed(window_ids):
            window_id = int(raw_window_id)
            title = _text_property(x11, display, window_id, "_NET_WM_NAME", "WM_NAME")
            if not title:
                continue
            try:
                bounds = _window_bounds(x11, display, root, window_id)
            except X11DesktopError:
                continue
            if bounds["width"] < 2 or bounds["height"] < 2:
                continue
            pid_property = _property(x11, display, window_id, "_NET_WM_PID")
            pid_values = pid_property[2] if pid_property and isinstance(pid_property[2], list) else []
            pid = int(pid_values[0]) if pid_values else 0
            process_name = ""
            executable = ""
            if pid > 0:
                with suppress(OSError):
                    process_name = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()[:200]
                with suppress(OSError):
                    executable = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
            windows.append({
                "window_id": window_id,
                "title": title[:300],
                "pid": pid,
                "process_name": process_name,
                "executable": executable,
                "bounds": bounds,
            })
        return windows[:200]


def window_bounds(window_id: int) -> dict[str, int]:
    with _display() as (x11, _xtst, display, root):
        return _window_bounds(x11, display, root, int(window_id))


def window_title(window_id: int) -> str:
    with _display() as (x11, _xtst, display, _root):
        return _text_property(x11, display, int(window_id), "_NET_WM_NAME", "WM_NAME")[:300]


def active_window() -> int | None:
    with _display() as (x11, _xtst, display, root):
        result = _property(x11, display, root, "_NET_ACTIVE_WINDOW")
        values = result[2] if result and isinstance(result[2], list) else []
        return int(values[0]) if values and values[0] else None


def activate_window(window_id: int) -> dict[str, Any]:
    target = int(window_id)
    with _display() as (x11, _xtst, display, root):
        _window_bounds(x11, display, root, target)
        x11.XMapRaised(display, Window(target))
        event = _XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.serial = 0
        event.xclient.send_event = True
        event.xclient.display = display
        event.xclient.window = Window(target)
        event.xclient.message_type = Atom(_atom(x11, display, "_NET_ACTIVE_WINDOW"))
        event.xclient.format = 32
        event.xclient.data.l[0] = 2  # source indication: pager/application
        event.xclient.data.l[1] = 0  # CurrentTime
        event.xclient.data.l[2] = 0
        mask = (1 << 20) | (1 << 19)  # SubstructureRedirect/NotifyMask
        x11.XSendEvent(display, Window(root), False, mask, ctypes.byref(event))
        x11.XFlush(display)
    time.sleep(0.08)
    return {
        "activated": active_window() == target,
        "window_id": target,
        "title": window_title(target),
    }


def cursor_position() -> dict[str, int]:
    with _display() as (x11, _xtst, display, root):
        root_return = Window()
        child_return = Window()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        window_x = ctypes.c_int()
        window_y = ctypes.c_int()
        mask = ctypes.c_uint()
        if not x11.XQueryPointer(
            display,
            Window(root),
            ctypes.byref(root_return),
            ctypes.byref(child_return),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(window_x),
            ctypes.byref(window_y),
            ctypes.byref(mask),
        ):
            raise X11DesktopError("Unable to read the X11 cursor position")
        return {"x": root_x.value, "y": root_y.value}


def _flush(x11: ctypes.CDLL, display: ctypes.c_void_p) -> None:
    x11.XFlush(display)
    x11.XSync(display, False)


def move_cursor(x: int, y: int) -> dict[str, int]:
    bounds = screen_bounds()
    target_x, target_y = int(x), int(y)
    if not (
        bounds["left"] <= target_x < bounds["left"] + bounds["width"]
        and bounds["top"] <= target_y < bounds["top"] + bounds["height"]
    ):
        raise X11DesktopError("Target coordinates are outside the X11 desktop")
    with _display() as (x11, xtst, display, _root):
        if not xtst.XTestFakeMotionEvent(display, -1, target_x, target_y, 0):
            raise X11DesktopError("XTest rejected the pointer movement")
        _flush(x11, display)
    return cursor_position()


_BUTTONS = {"left": 1, "middle": 2, "right": 3}


def mouse_button(button: str, pressed: bool) -> None:
    number = _BUTTONS.get(str(button).lower())
    if number is None:
        raise X11DesktopError(f"Unsupported mouse button: {button}")
    with _display() as (x11, xtst, display, _root):
        if not xtst.XTestFakeButtonEvent(display, number, bool(pressed), 0):
            raise X11DesktopError("XTest rejected the mouse button event")
        _flush(x11, display)


def click(button: str = "left", count: int = 1) -> None:
    normalized_count = int(count)
    if not 1 <= normalized_count <= 3:
        raise X11DesktopError("click count must be between 1 and 3")
    for index in range(normalized_count):
        mouse_button(button, True)
        mouse_button(button, False)
        if index + 1 < normalized_count:
            time.sleep(0.08)


_KEY_ALIASES = {
    "ctrl": "Control_L",
    "control": "Control_L",
    "control_l": "Control_L",
    "shift": "Shift_L",
    "shift_l": "Shift_L",
    "alt": "Alt_L",
    "alt_l": "Alt_L",
    "option": "Alt_L",
    "win": "Super_L",
    "windows": "Super_L",
    "super": "Super_L",
    "meta": "Super_L",
    "cmd": "Super_L",
    "command": "Super_L",
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "space": "space",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "page_up": "Page_Up",
    "pagedown": "Page_Down",
    "page_down": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


def _keysym_for_name(x11: ctypes.CDLL, value: str) -> int:
    normalized = value.strip()
    alias = _KEY_ALIASES.get(normalized.casefold(), normalized)
    keysym = int(x11.XStringToKeysym(alias.encode("utf-8")))
    if keysym == 0 and len(alias) == 1:
        keysym = ord(alias)
    return keysym


def _keycode(
    x11: ctypes.CDLL,
    display: ctypes.c_void_p,
    value: str,
) -> tuple[int, bool]:
    keysym = _keysym_for_name(x11, value)
    if keysym == 0:
        raise X11DesktopError(f"Unsupported key: {value}")
    keycode = int(x11.XKeysymToKeycode(display, keysym))
    if keycode == 0:
        raise X11DesktopError(f"The current keyboard map has no key for: {value}")
    level_zero = int(x11.XKeycodeToKeysym(display, keycode, 0))
    level_one = int(x11.XKeycodeToKeysym(display, keycode, 1))
    needs_shift = keysym != level_zero and keysym == level_one
    return keycode, needs_shift


def _fake_key(
    xtst: ctypes.CDLL,
    display: ctypes.c_void_p,
    keycode: int,
    pressed: bool,
) -> None:
    if not xtst.XTestFakeKeyEvent(display, int(keycode), bool(pressed), 0):
        raise X11DesktopError("XTest rejected the keyboard event")


def _chord_parts(chord: str) -> list[str]:
    value = str(chord or "").strip()
    if not value:
        raise X11DesktopError("A key or key chord is required")
    parts = [part.strip() for part in value.replace("-", "+").split("+") if part.strip()]
    if not parts:
        raise X11DesktopError("A key or key chord is required")
    return parts


def key_down(chord: str) -> list[int]:
    parts = _chord_parts(chord)
    with _display() as (x11, xtst, display, _root):
        codes: list[int] = []
        for part in parts:
            code, needs_shift = _keycode(x11, display, part)
            if needs_shift and not any(
                _KEY_ALIASES.get(item.casefold(), item) == "Shift_L"
                for item in parts[:-1]
            ):
                shift_code, _ = _keycode(x11, display, "Shift_L")
                if shift_code not in codes:
                    _fake_key(xtst, display, shift_code, True)
                    codes.append(shift_code)
            _fake_key(xtst, display, code, True)
            codes.append(code)
        _flush(x11, display)
        return codes


def key_up_codes(codes: Sequence[int]) -> None:
    with _display() as (x11, xtst, display, _root):
        for code in reversed([int(value) for value in codes]):
            _fake_key(xtst, display, code, False)
        _flush(x11, display)


def press_key(chord: str) -> None:
    codes = key_down(chord)
    try:
        time.sleep(0.015)
    finally:
        key_up_codes(codes)


def _type_ascii_character(
    x11: ctypes.CDLL,
    xtst: ctypes.CDLL,
    display: ctypes.c_void_p,
    character: str,
) -> None:
    if character == "\n":
        key_name = "Return"
    elif character == "\t":
        key_name = "Tab"
    elif character == " ":
        key_name = "space"
    else:
        key_name = character
    keycode, needs_shift = _keycode(x11, display, key_name)
    shift_code = 0
    if needs_shift:
        shift_code, _ = _keycode(x11, display, "Shift_L")
        _fake_key(xtst, display, shift_code, True)
    _fake_key(xtst, display, keycode, True)
    _fake_key(xtst, display, keycode, False)
    if shift_code:
        _fake_key(xtst, display, shift_code, False)


def type_text(text: str) -> None:
    value = str(text)
    with _display() as (x11, xtst, display, _root):
        for character in value:
            if character in {"\n", "\t"} or 0x20 <= ord(character) <= 0x7E:
                _type_ascii_character(x11, xtst, display, character)
            else:
                # Ctrl+Shift+U followed by the Unicode code point is supported
                # by GTK, Qt and Chromium text fields on mainstream Linux.
                ctrl_code, _ = _keycode(x11, display, "Control_L")
                shift_code, _ = _keycode(x11, display, "Shift_L")
                u_code, _ = _keycode(x11, display, "u")
                _fake_key(xtst, display, ctrl_code, True)
                _fake_key(xtst, display, shift_code, True)
                _fake_key(xtst, display, u_code, True)
                _fake_key(xtst, display, u_code, False)
                _fake_key(xtst, display, shift_code, False)
                _fake_key(xtst, display, ctrl_code, False)
                for digit in f"{ord(character):x}":
                    _type_ascii_character(x11, xtst, display, digit)
                _type_ascii_character(x11, xtst, display, "\n")
            x11.XFlush(display)
            time.sleep(0.001)
        x11.XSync(display, False)


def scroll(delta_x: int = 0, delta_y: int = 0) -> dict[str, int]:
    horizontal = max(-5_000, min(5_000, int(delta_x)))
    vertical = max(-5_000, min(5_000, int(delta_y)))

    def clicks(amount: int) -> int:
        return 0 if amount == 0 else max(1, min(50, math.ceil(abs(amount) / 100)))

    # X11 buttons: 4 up, 5 down, 6 left, 7 right. Positive deltas match
    # viewport movement (positive y means scroll down), like Anthropic's tool.
    events = [
        (5 if vertical > 0 else 4, clicks(vertical)),
        (7 if horizontal > 0 else 6, clicks(horizontal)),
    ]
    with _display() as (x11, xtst, display, _root):
        for button, count in events:
            for _ in range(count):
                _fake = xtst.XTestFakeButtonEvent
                if not _fake(display, button, True, 0) or not _fake(display, button, False, 0):
                    raise X11DesktopError("XTest rejected the scroll event")
        _flush(x11, display)
    return {"scroll_x": horizontal, "scroll_y": vertical}


def drag(start: tuple[int, int], end: tuple[int, int], duration_ms: int = 500) -> None:
    duration = max(100, min(5_000, int(duration_ms))) / 1000
    steps = max(5, min(100, int(duration * 60)))
    move_cursor(*start)
    mouse_button("left", True)
    try:
        for step in range(1, steps + 1):
            ratio = step / steps
            move_cursor(
                round(start[0] + (end[0] - start[0]) * ratio),
                round(start[1] + (end[1] - start[1]) * ratio),
            )
            time.sleep(duration / steps)
    finally:
        mouse_button("left", False)
