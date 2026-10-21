"""Tightly-bounded Windows UI Automation tools.

This is intentionally not a general desktop-control API.  The model can only
inspect the foreground window, then click, type or press ordinary app keys in
that exact window using the opaque handle and process id returned by the
inspection.  Every UI action asks for approval, can never be pre-approved, and
is rejected if the window disappeared, changed process, changed title, is a
command shell or system dialog, or cannot be made foreground.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain.ports import Risk
from .apps import score as _app_score


ULONG_PTR = wintypes.WPARAM

# The INPUT union must contain every member so that sizeof(INPUT) matches what Windows expects
# (40 bytes on 64-bit). A union with only the keyboard member is 32 bytes and SendInput rejects it.


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyBdInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

# Never type into, or send keys to, command interpreters, script hosts or system/security dialogs.
# Text plus Enter in one of these is command execution, which this tool must not become.
BLOCKED_PROCESSES = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "windowsterminal.exe", "wt.exe",
    "conhost.exe", "openconsole.exe", "wsl.exe", "bash.exe", "mintty.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "regedit.exe", "mmc.exe", "taskmgr.exe", "consent.exe", "securityhealthsystray.exe",
})

# Shortcuts that leave the approved window or reach into the system rather than the app.
BLOCKED_COMBOS = frozenset({
    ("tab", frozenset({"alt"})), ("tab", frozenset({"alt", "shift"})), ("escape", frozenset({"alt"})),
    ("escape", frozenset({"ctrl"})), ("escape", frozenset({"ctrl", "shift"})),
    ("delete", frozenset({"ctrl", "alt"})),
})


@dataclass(frozen=True)
class Window:
    handle: int
    process_id: int
    title: str
    client_width: int
    client_height: int
    process_name: str = ""

    @property
    def window_id(self) -> str:
        return f"{self.handle:x}:{self.process_id}"


WINDOW_MATCH_THRESHOLD = 0.5  # same floor find_apps uses when listing candidates


def match_window(query: str, windows: list["Window"], minimum: float = WINDOW_MATCH_THRESHOLD) -> "Window | None":
    """Find the open window whose title or process best matches `query`.

    Uses the same fuzzy name matching as installed-app search (apps.score), so it works for any
    running application the user names, not a fixed list of known apps."""
    if not windows:
        return None

    def best_score(w: "Window") -> float:
        process = w.process_name.rsplit(".", 1)[0] if w.process_name else ""
        return max(_app_score(query, w.title), _app_score(query, process) if process else 0.0)

    ranked = sorted(windows, key=best_score, reverse=True)
    top = ranked[0]
    return top if best_score(top) >= minimum else None


def invalid_text_reason(text: str) -> str | None:
    """Typed text must be plain characters. New lines, tabs and other control characters could act as Enter
    or other keys; submitting is a separate, separately approved key press."""
    for ch in text:
        if unicodedata.category(ch) in {"Cc", "Zl", "Zp"}:
            return ("text may not contain new lines, tabs or other control characters; "
                    "use the key tool to press Enter")
    return None


class Desktop(Protocol):
    def foreground(self) -> Window | None: ...
    def find(self, window_id: str) -> Window | None: ...
    def find_by_title_or_process(self, name: str) -> Window | None: ...
    def list_windows(self) -> list[Window]: ...
    def activate(self, window: Window) -> bool: ...
    def click(self, window: Window, x: int, y: int) -> None: ...
    def type_text(self, window: Window, text: str) -> None: ...
    def press_key(self, window: Window, key_code: int, modifier_codes: list[int]) -> None: ...
    def read_screen(self, window: Window, find_text: str = "") -> dict[str, Any]: ...


class Win32Desktop:
    """The small Win32 surface needed for safe bounded input (stdlib only)."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows UI control is available only on Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        try:
            hdesk = self.user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                self.user32.SetThreadDesktop(hdesk)
        except Exception:
            pass
        self._declare_signatures()
        self._set_dpi_awareness()

    def _declare_signatures(self) -> None:
        """Explicit types, so 64-bit window handles are never truncated to 32-bit ints."""
        u, k, w = self.user32, self.kernel32, wintypes
        u.GetForegroundWindow.restype, u.GetForegroundWindow.argtypes = w.HWND, []
        u.IsWindow.restype, u.IsWindow.argtypes = w.BOOL, [w.HWND]
        u.IsWindowVisible.restype, u.IsWindowVisible.argtypes = w.BOOL, [w.HWND]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.GetWindowTextLengthW.restype, u.GetWindowTextLengthW.argtypes = ctypes.c_int, [w.HWND]
        u.GetWindowTextW.restype, u.GetWindowTextW.argtypes = ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]
        u.GetClientRect.restype, u.GetClientRect.argtypes = w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)]
        u.ClientToScreen.restype, u.ClientToScreen.argtypes = w.BOOL, [w.HWND, ctypes.POINTER(w.POINT)]
        u.ShowWindow.restype, u.ShowWindow.argtypes = w.BOOL, [w.HWND, ctypes.c_int]
        u.SetForegroundWindow.restype, u.SetForegroundWindow.argtypes = w.BOOL, [w.HWND]
        u.BringWindowToTop.restype, u.BringWindowToTop.argtypes = w.BOOL, [w.HWND]
        u.SetActiveWindow.restype, u.SetActiveWindow.argtypes = w.HWND, [w.HWND]
        u.SetFocus.restype, u.SetFocus.argtypes = w.HWND, [w.HWND]
        u.EnumWindows.restype, u.EnumWindows.argtypes = w.BOOL, [ctypes.c_void_p, wintypes.LPARAM]
        u.AttachThreadInput.restype, u.AttachThreadInput.argtypes = w.BOOL, [w.DWORD, w.DWORD, w.BOOL]
        u.SetCursorPos.restype, u.SetCursorPos.argtypes = w.BOOL, [ctypes.c_int, ctypes.c_int]
        u.mouse_event.restype = None
        u.mouse_event.argtypes = [w.DWORD, w.DWORD, w.DWORD, w.DWORD, ULONG_PTR]
        u.SendInput.restype, u.SendInput.argtypes = w.UINT, [w.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        k.GetCurrentThreadId.restype, k.GetCurrentThreadId.argtypes = w.DWORD, []
        k.OpenProcess.restype, k.OpenProcess.argtypes = w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]
        k.QueryFullProcessImageNameW.restype = w.BOOL
        k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        k.CloseHandle.restype, k.CloseHandle.argtypes = w.BOOL, [w.HANDLE]


    @staticmethod
    def _set_dpi_awareness() -> None:
        """Makes client coordinates and screen coordinates agree on scaled displays."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        except (OSError, AttributeError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (OSError, AttributeError):
                pass

    def _process_name(self, pid: int) -> str:
        handle = self.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value).lower()
            return ""
        finally:
            self.kernel32.CloseHandle(handle)

    def _window(self, hwnd: int | None) -> Window | None:
        if not hwnd or not self.user32.IsWindow(hwnd) or not self.user32.IsWindowVisible(hwnd):
            return None
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = self.user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, title, len(title))
        rect = wintypes.RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        return Window(int(hwnd), pid.value, title.value or "(untitled)", rect.right - rect.left,
                      rect.bottom - rect.top, self._process_name(pid.value))

    def foreground(self) -> Window | None:
        return self._window(self.user32.GetForegroundWindow())

    def list_windows(self) -> list[Window]:
        windows: list[Window] = []
        def _enum(hwnd: int, _: int) -> bool:
            if self.user32.IsWindow(hwnd) and self.user32.IsWindowVisible(hwnd):
                w = self._window(hwnd)
                if w and w.title and w.title != "(untitled)" and w.client_width > 50 and w.client_height > 50:
                    windows.append(w)
            return True
        cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user32.EnumWindows(cb_type(_enum), 0)
        return windows

    def find_by_title_or_process(self, name: str) -> Window | None:
        """Works for any running application: fuzzy-matches `name` against open windows' titles and
        process names with the same scorer used to find apps to launch (see match_window)."""
        return match_window(name, self.list_windows())

    def find(self, window_id: str) -> Window | None:
        try:
            handle_text, pid_text = window_id.split(":", 1)
            hwnd, expected_pid = int(handle_text, 16), int(pid_text)
        except (AttributeError, TypeError, ValueError):
            return None
        window = self._window(hwnd)
        return window if window and window.process_id == expected_pid else None

    def activate(self, window: Window) -> bool:
        self.user32.ShowWindow(window.handle, 9)  # SW_RESTORE, if minimized
        fg_hwnd = self.user32.GetForegroundWindow()
        if fg_hwnd != window.handle:
            fg_thread = self.user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            curr_thread = self.kernel32.GetCurrentThreadId()
            if fg_thread and fg_thread != curr_thread:
                self.user32.AttachThreadInput(curr_thread, fg_thread, True)
                try:
                    self.user32.SetForegroundWindow(window.handle)
                    self.user32.BringWindowToTop(window.handle)
                    self.user32.SetActiveWindow(window.handle)
                    self.user32.SetFocus(window.handle)
                finally:
                    self.user32.AttachThreadInput(curr_thread, fg_thread, False)
            else:
                self.user32.SetForegroundWindow(window.handle)
                self.user32.BringWindowToTop(window.handle)
                self.user32.SetActiveWindow(window.handle)
                self.user32.SetFocus(window.handle)
        for _ in range(25):  # give Windows a moment to complete the focus change
            if self.user32.GetForegroundWindow() == window.handle:
                return True
            time.sleep(0.02)
        return self.user32.GetForegroundWindow() == window.handle

    def _client_screen_point(self, window: Window, x: int, y: int):
        point = wintypes.POINT(x, y)
        if not self.user32.ClientToScreen(window.handle, ctypes.byref(point)):
            raise RuntimeError("could not translate the target coordinate")
        return point

    def click(self, window: Window, x: int, y: int) -> None:
        point = self._client_screen_point(window, x, y)
        self.user32.SetCursorPos(point.x, point.y)
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up

    def _send(self, events: list[tuple[int, int, int]]) -> None:
        """events: (virtual key, scan code, flags) keyboard events, sent in one call."""
        inputs = (_Input * len(events))()
        for i, (vk, scan, flags) in enumerate(events):
            inputs[i] = _Input(INPUT_KEYBOARD, _InputUnion(ki=_KeyBdInput(vk, scan, flags, 0, 0)))
        sent = self.user32.SendInput(len(inputs), inputs, ctypes.sizeof(_Input))
        if sent != len(inputs):
            raise RuntimeError(f"Windows rejected some keyboard input (sent {sent} of {len(inputs)})")

    def type_text(self, window: Window, text: str) -> None:
        # Defence in depth: the tool already validated this, and the desktop layer re-checks.
        if (reason := invalid_text_reason(text)) is not None:
            raise ValueError(reason)
        data = text.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        events: list[tuple[int, int, int]] = []
        for unit in units:  # KEYEVENTF_UNICODE types literal characters; it cannot express shortcuts
            events.append((0, unit, KEYEVENTF_UNICODE))
            events.append((0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        self._send(events)

    def press_key(self, window: Window, key_code: int, modifier_codes: list[int]) -> None:
        """Send one ordinary app key or shortcut. The caller has already focused the approved window."""
        events = [(code, 0, 0) for code in modifier_codes]
        events += [(key_code, 0, 0), (key_code, 0, KEYEVENTF_KEYUP)]
        events += [(code, 0, KEYEVENTF_KEYUP) for code in reversed(modifier_codes)]
        self._send(events)

    def read_screen(self, window: Window, find_text: str = "") -> dict[str, Any]:
        """Inspect visible UI elements, text labels, and buttons in the window."""
        elements: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        # 1. Enumerate standard child windows/controls
        child_hwnds: list[int] = []

        def _enum_cb(hwnd: int, _: int) -> bool:
            child_hwnds.append(hwnd)
            return True

        cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user32.EnumChildWindows(window.handle, cb_type(_enum_cb), 0)

        for ch in child_hwnds:
            if not self.user32.IsWindowVisible(ch):
                continue
            length = self.user32.GetWindowTextLengthW(ch)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                self.user32.GetWindowTextW(ch, buf, len(buf))
                txt = buf.value.strip()
                if txt and txt not in seen_texts:
                    seen_texts.add(txt)
                    rect = wintypes.RECT()
                    if self.user32.GetWindowRect(ch, ctypes.byref(rect)):
                        pt_tl = wintypes.POINT(rect.left, rect.top)
                        self.user32.ScreenToClient(window.handle, ctypes.byref(pt_tl))
                        pt_br = wintypes.POINT(rect.right, rect.bottom)
                        self.user32.ScreenToClient(window.handle, ctypes.byref(pt_br))
                        w, h = max(0, pt_br.x - pt_tl.x), max(0, pt_br.y - pt_tl.y)
                        cx, cy = pt_tl.x + w // 2, pt_tl.y + h // 2
                        item = {"text": txt, "x": pt_tl.x, "y": pt_tl.y, "width": w, "height": h, "click_x": cx, "click_y": cy}
                        elements.append(item)
                        if find_text and find_text.lower() in txt.lower():
                            found.append(item)

        # 2. OCR screen capture using GDI + pytesseract for modern/rendered apps (Electron, Chromium, etc.)
        # The capture reads from the *screen* at this window's rectangle, not from the window itself, so
        # it must still be the foreground window or another app's pixels could be captured instead.
        if self.user32.GetForegroundWindow() != window.handle:
            return {"elements": elements, "found": found, "visible_text": " ".join(e["text"] for e in elements)[:3000]}
        try:
            import win32gui, win32ui
            from PIL import Image
            import pytesseract

            rect = wintypes.RECT()
            self.user32.GetWindowRect(window.handle, ctypes.byref(rect))
            rx, ry = rect.left, rect.top
            rw = max(10, min(rect.right - rect.left, 3840))
            rh = max(10, min(rect.bottom - rect.top, 2160))

            hdc = win32gui.GetDC(0)
            mfcDC = win32ui.CreateDCFromHandle(hdc)
            saveDC = mfcDC.CreateCompatibleDC()
            saveBitMap = win32ui.CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDC, rw, rh)
            saveDC.SelectObject(saveBitMap)
            self.gdi32.BitBlt(saveDC.GetSafeHdc(), 0, 0, rw, rh, hdc, rx, ry, 0x00CC0020)

            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)
            im = Image.frombuffer('RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']), bmpstr, 'raw', 'BGRX', 0, 1)

            win32gui.DeleteObject(saveBitMap.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(0, hdc)

            ocr_data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)
            for i in range(len(ocr_data.get("text", []))):
                word = str(ocr_data["text"][i]).strip()
                if not word or word in seen_texts:
                    continue
                seen_texts.add(word)
                ox, oy = int(ocr_data["left"][i]), int(ocr_data["top"][i])
                ow, oh = int(ocr_data["width"][i]), int(ocr_data["height"][i])
                ocx, ocy = ox + ow // 2, oy + oh // 2
                item = {"text": word, "x": ox, "y": oy, "width": ow, "height": oh, "click_x": ocx, "click_y": ocy}
                elements.append(item)
                if find_text and find_text.lower() in word.lower():
                    found.append(item)
        except Exception:
            pass

        visible_words = [e["text"] for e in elements]
        return {
            "elements": elements[:60],
            "found": found[:10],
            "visible_text": " ".join(visible_words)[:3000],
        }


class _WindowTool:
    always_approve = True

    def __init__(self, desktop: Desktop):
        self._desktop = desktop

    @staticmethod
    def _target(desktop: Desktop, window_id: Any, window_title: Any) -> tuple[Window | None, dict[str, Any] | None]:
        window = desktop.find(str(window_id))
        if not window:
            return None, {"ok": False, "error": "target window no longer exists; inspect the active window again"}
        if window.title != str(window_title):
            return None, {"ok": False, "error": "target window title changed; inspect it again before sending input"}
        if window.process_name in BLOCKED_PROCESSES:
            return None, {"ok": False, "summary": "Jarvis won't send input to a command shell or system dialog",
                          "error": f"input into {window.process_name} is not allowed (command shells and system "
                                   "dialogs are off limits); tell the user"}
        if not desktop.activate(window):
            return None, {"ok": False, "error": "could not focus the approved target window; no input was sent"}
        # Re-read after focus to prevent handle reuse or a focus race.
        current = desktop.foreground()
        if not current or current.window_id != window.window_id:
            return None, {"ok": False, "error": "target focus changed before input; no input was sent"}
        return current, None


def _locate(desktop: Desktop, window: Window, find_text: str) -> tuple[tuple[int, int] | None, dict[str, Any] | None]:
    """Finds `find_text` on screen right now. Used instead of a coordinate read earlier in the same
    conversation turn, since that coordinate can go stale if the window's content moved or scrolled
    (for example a chat panel) in the time between looking and acting."""
    reader = getattr(desktop, "read_screen", None)
    if not reader:
        return None, {"ok": False, "error": "screen reading is unavailable on this desktop"}
    found = reader(window, find_text).get("found", [])
    if not found:
        return None, {"ok": False, "error": f"no visible element matches '{find_text}' right now; nothing was sent"}
    return (found[0]["click_x"], found[0]["click_y"]), None


_KEYS = {
    "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
_MODIFIERS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}


def _key_code(value: Any) -> int | None:
    key = str(value).strip().lower()
    if key in _KEYS:
        return _KEYS[key]
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        return ord(key.upper())
    return None


def _modifier_codes(values: Any) -> list[int] | None:
    if not isinstance(values, list) or len(values) != len(set(values)):
        return None
    try:
        return [_MODIFIERS[str(value).lower()] for value in values]
    except KeyError:
        return None


def is_blocked_combo(key: Any, modifiers: Any) -> bool:
    return (str(key).strip().lower(), frozenset(str(m).lower() for m in modifiers or [])) in BLOCKED_COMBOS


class FocusWindow(_WindowTool):
    name = "focus_window"
    description = (
        "Bring an open desktop app or window to the foreground and focus it by name "
        "(for example 'Claude', 'Notepad', 'Visual Studio Code'). "
        "Returns the window id, title, and client size so you can interact with it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name or title of the app to bring to foreground (e.g. 'Claude')",
            }
        },
        "required": ["app_name"],
        "additionalProperties": False,
    }
    risk = Risk.CHANGE

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        app_name = str(args.get("app_name", "")).strip()
        finder = getattr(self._desktop, "find_by_title_or_process", None)
        if not finder:
            return {"ok": False, "error": "window search not supported on this desktop"}
        win = finder(app_name)
        if not win:
            return {"ok": False, "error": f"no open window found matching '{app_name}'"}
        if not self._desktop.activate(win):
            return {"ok": False, "error": f"could not bring '{win.title}' to foreground"}
        return {
            "ok": True,
            "window": {
                "id": win.window_id,
                "title": win.title,
                "client_size": {"width": win.client_width, "height": win.client_height},
            },
            "summary": f"Brought “{win.title}” to the foreground",
        }


class InspectActiveWindow(_WindowTool):
    name = "inspect_active_window"
    description = (
        "Inspect the currently active Windows app before a UI action. Returns an opaque window id, "
        "title, and client-area size. If target_app is provided, focuses that app first. "
        "Never targets or inspects the Jarvis UI browser window."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target_app": {
                "type": "string",
                "description": "Optional: app or window name to focus and inspect (e.g. 'Claude', 'Notepad')",
            }
        },
        "additionalProperties": False,
    }
    risk = Risk.READ

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target_app", "")).strip()
        if target and hasattr(self._desktop, "find_by_title_or_process"):
            win = self._desktop.find_by_title_or_process(target)
            if win:
                self._desktop.activate(win)
        window = self._desktop.foreground()
        if not window:
            return {"ok": False, "error": "no visible foreground window"}
        if "jarvis" in window.title.lower():
            if hasattr(self._desktop, "list_windows"):
                other_windows = [
                    w for w in self._desktop.list_windows()
                    if "jarvis" not in w.title.lower() and w.process_name not in BLOCKED_PROCESSES
                ]
                if other_windows:
                    self._desktop.activate(other_windows[0])
                    window = self._desktop.foreground() or other_windows[0]
        return {"ok": True, "window": {"id": window.window_id, "title": window.title,
                                         "client_size": {"width": window.client_width, "height": window.client_height}},
                "summary": f"Looked at the window “{window.title}”"}


class ClickInWindow(_WindowTool):
    name = "click_in_window"
    description = ("Click a point in a previously inspected Windows app. Give exact x,y coordinates, or give "
                   "find_text to click whatever visible element contains that text: the element's position is "
                   "found fresh at the moment of the click, which is safer than clicking coordinates read "
                   "earlier in windows whose content can move or scroll, such as a chat panel.")
    parameters = {"type": "object", "properties": {
        "window_id": {"type": "string"}, "window_title": {"type": "string"},
        "x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
        "find_text": {"type": "string", "description": "Click the element containing this visible text, located "
                                                        "right before the click instead of from an earlier look"}},
        "required": ["window_id", "window_title"], "additionalProperties": False}
    risk = Risk.CHANGE

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        window, failure = self._target(self._desktop, args.get("window_id"), args.get("window_title"))
        if failure:
            return failure
        find_text = str(args.get("find_text", "")).strip()
        if find_text:
            target, failure = _locate(self._desktop, window, find_text)
            if failure:
                return failure
            x, y = target
        else:
            try:
                x, y = int(args["x"]), int(args["y"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "error": "give x and y as integers, or give find_text instead"}
        if not (0 <= x < window.client_width and 0 <= y < window.client_height):
            return {"ok": False, "error": "coordinate is outside the approved target window"}
        self._desktop.click(window, x, y)
        label = f" matching “{find_text}”" if find_text else ""
        return {"ok": True, "clicked": {"window_id": window.window_id, "x": x, "y": y, "matched_text": find_text or None},
                "summary": f"Clicked{label} in “{window.title}”"}


class TypeInWindow(_WindowTool):
    name = "type_in_window"
    description = ("Type literal text into the focused control of a previously inspected Windows app. "
                   "To click into the right input box first, prefer find_text naming a nearby visible label or "
                   "placeholder: its position is located fresh right before the click, which stays correct even if "
                   "the window's content moved or scrolled since you last looked. Only use the raw click_x/click_y "
                   "coordinates when you have no visible text to anchor on. "
                   "Optionally set press_enter to true to type and immediately press Enter in one single flow.")
    parameters = {"type": "object", "properties": {
        "window_id": {"type": "string"}, "window_title": {"type": "string"},
        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
        "find_text": {"type": "string", "description": "Click the element containing this visible text before "
                                                        "typing, located fresh right before the click"},
        "click_x": {"type": "integer", "minimum": 0, "description": "Fallback X coordinate to click before typing, "
                                                                    "only when find_text does not apply"},
        "click_y": {"type": "integer", "minimum": 0, "description": "Fallback Y coordinate to click before typing, "
                                                                    "only when find_text does not apply"},
        "press_enter": {"type": "boolean", "description": "If true, automatically presses Enter after typing to submit in one flow"},
    },
        "required": ["window_id", "window_title", "text"], "additionalProperties": False}
    risk = Risk.CHANGE

    def audit_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"window_id": args.get("window_id"), "window_title": args.get("window_title"),
                "text_length": len(str(args.get("text", "")))}

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str) or not text or len(text) > 2000:
            return {"ok": False, "error": "text must be between 1 and 2000 characters"}
        if (reason := invalid_text_reason(text)) is not None:
            return {"ok": False, "error": reason}
        window, failure = self._target(self._desktop, args.get("window_id"), args.get("window_title"))
        if failure:
            return failure
        find_text = str(args.get("find_text", "")).strip()
        if find_text:
            target, click_failure = _locate(self._desktop, window, find_text)
            if click_failure:
                return click_failure
            self._desktop.click(window, *target)
            time.sleep(0.05)
        elif "click_x" in args and "click_y" in args:
            try:
                cx, cy = int(args["click_x"]), int(args["click_y"])
                if 0 <= cx < window.client_width and 0 <= cy < window.client_height:
                    self._desktop.click(window, cx, cy)
                    time.sleep(0.05)
            except (ValueError, TypeError):
                pass
        self._desktop.type_text(window, text)
        pressed_enter = bool(args.get("press_enter"))
        if pressed_enter:
            time.sleep(0.05)
            press = getattr(self._desktop, "press_key", None)
            if press:
                press(window, 0x0D, [])  # Enter
        summary = f"Typed {len(text)} characters" + (" and pressed Enter" if pressed_enter else "") + f" into “{window.title}”"
        return {"ok": True, "typed": {"window_id": window.window_id, "characters": len(text), "submitted": pressed_enter},
                "summary": summary}



class PressKeyInWindow(_WindowTool):
    name = "press_key_in_window"
    description = ("Press one app key or app shortcut in a previously inspected Windows app, such as Enter to "
                   "submit text or Ctrl+N to create a new conversation. Every press needs its own approval. "
                   "Windows-key and system shortcuts (Alt+Tab, Ctrl+Esc, Ctrl+Shift+Esc) are not supported.")
    parameters = {"type": "object", "properties": {"window_id": {"type": "string"}, "window_title": {"type": "string"},
        "key": {"type": "string", "description": "Enter, Tab, Escape, arrows, Home, End, PageUp, PageDown, F1-F12, A-Z, or 0-9"},
        "modifiers": {"type": "array", "items": {"type": "string", "enum": ["ctrl", "alt", "shift"]}, "uniqueItems": True}},
        "required": ["window_id", "window_title", "key", "modifiers"], "additionalProperties": False}
    risk = Risk.CHANGE

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        key_code, modifier_codes = _key_code(args.get("key")), _modifier_codes(args.get("modifiers"))
        if key_code is None or modifier_codes is None:
            return {"ok": False, "error": "unsupported key or modifier; use a normal app key with ctrl, alt, and/or shift"}
        if is_blocked_combo(args.get("key"), args.get("modifiers")):
            return {"ok": False, "error": "that shortcut reaches outside the app and is not allowed"}
        window, failure = self._target(self._desktop, args.get("window_id"), args.get("window_title"))
        if failure:
            return failure
        press = getattr(self._desktop, "press_key", None)
        if not press:
            return {"ok": False, "error": "keyboard input is unavailable for this desktop"}
        press(window, key_code, modifier_codes)
        combo = " + ".join([str(m).title() for m in args["modifiers"]] + [str(args["key"]).title()])
        return {"ok": True, "pressed": {"window_id": window.window_id, "key": str(args["key"]).lower(),
                                         "modifiers": [str(m).lower() for m in args["modifiers"]]},
                "summary": f"Pressed {combo} in “{window.title}”"}


class ReadScreen(_WindowTool):
    name = "read_screen"
    description = (
        "Read visible text, buttons, and controls from a previously inspected Windows app. "
        "Returns the text and buttons on screen along with their click coordinates (click_x, click_y) "
        "so you can find and click buttons (like 'New Chat', 'Start conversation', 'Submit') by yourself."
    )
    parameters = {
        "type": "object",
        "properties": {
            "window_id": {"type": "string"},
            "window_title": {"type": "string"},
            "find_text": {
                "type": "string",
                "description": "Optional text to search for on screen (e.g. 'New', 'Start', 'Send', 'Submit')",
            },
        },
        "required": ["window_id", "window_title"],
        "additionalProperties": False,
    }
    risk = Risk.READ

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        window, failure = self._target(self._desktop, args.get("window_id"), args.get("window_title"))
        if failure:
            return failure
        find_text = str(args.get("find_text", "")).strip()
        reader = getattr(self._desktop, "read_screen", None)
        if not reader:
            return {"ok": False, "error": "screen reading is unavailable on this desktop"}
        data = reader(window, find_text)
        found = data.get("found", [])
        return {
            "ok": True,
            "window_id": window.window_id,
            "found": found,
            "elements": data.get("elements", [])[:30],
            "visible_text": data.get("visible_text", "")[:2000],
            "summary": f"Read screen of “{window.title}”" + (f" (found {len(found)} matches for '{find_text}')" if find_text else ""),
        }

