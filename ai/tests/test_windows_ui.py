import asyncio
import ctypes
import os

import pytest

from jarvis_ai.application.tool_executor import ToolExecutor
from jarvis_ai.config import Settings
from jarvis_ai.policy.approval import GRANTABLE, SessionPolicy
from jarvis_ai.tools import windows_ui as w
from jarvis_ai.tools.registry import ToolRegistry


class FakeDesktop:
    def __init__(self, process_name="notes.exe", title="Notes"):
        self.window = w.Window(0x1234, 88, title, 600, 400, process_name)
        self.typed, self.pressed, self.clicks = [], [], []

    def foreground(self):
        return self.window

    def find(self, window_id):
        return self.window if window_id == self.window.window_id else None

    def activate(self, window):
        return True

    def click(self, window, x, y):
        self.clicks.append((x, y))

    def type_text(self, window, text):
        self.typed.append(text)

    def press_key(self, window, key_code, modifier_codes):
        self.pressed.append((key_code, modifier_codes))


def args(**extra):
    return {"window_id": "1234:88", "window_title": "Notes", **extra}


# ---- fresh-lookup click/type: closes the stale-coordinate race on dynamic screens (e.g. chat panels) ----
class ScreenDesktop(FakeDesktop):
    """A FakeDesktop whose read_screen result can change between calls, like a live chat panel."""

    def __init__(self):
        super().__init__()
        self.screens = []  # queue of {"found": [...]}; each read_screen call pops the next one

    def read_screen(self, window, find_text=""):
        return self.screens.pop(0) if self.screens else {"found": [], "elements": [], "visible_text": ""}


def test_click_by_find_text_locates_fresh_right_before_clicking():
    desktop = ScreenDesktop()
    desktop.screens.append({"found": [{"text": "Agent", "click_x": 40, "click_y": 55}], "elements": [], "visible_text": ""})
    result = w.ClickInWindow(desktop).run({**args(), "find_text": "Agent"})
    assert result["ok"] and desktop.clicks == [(40, 55)]
    assert result["clicked"]["matched_text"] == "Agent"


def test_click_by_find_text_reflects_content_that_moved_since_an_earlier_look():
    # Simulates: an earlier read_screen found the button at one spot, but by the time the click
    # tool actually runs (after model + approval round trips) the panel has scrolled.
    desktop = ScreenDesktop()
    stale_coords = (999, 999)  # what an earlier, separate read_screen call would have returned
    desktop.screens.append({"found": [{"text": "Agent", "click_x": 12, "click_y": 34}], "elements": [], "visible_text": ""})
    result = w.ClickInWindow(desktop).run({**args(), "find_text": "Agent"})
    assert desktop.clicks == [(12, 34)] and stale_coords not in desktop.clicks


def test_click_by_find_text_refuses_when_nothing_matches_right_now():
    desktop = ScreenDesktop()
    desktop.screens.append({"found": [], "elements": [], "visible_text": ""})
    result = w.ClickInWindow(desktop).run({**args(), "find_text": "Nonexistent"})
    assert not result["ok"] and desktop.clicks == []


def test_click_still_accepts_raw_coordinates_without_find_text():
    desktop = FakeDesktop()
    result = w.ClickInWindow(desktop).run({**args(), "x": 7, "y": 8})
    assert result["ok"] and desktop.clicks == [(7, 8)] and result["clicked"]["matched_text"] is None


def test_type_by_find_text_clicks_the_freshly_located_input_before_typing():
    desktop = ScreenDesktop()
    desktop.screens.append({"found": [{"text": "Message", "click_x": 15, "click_y": 25}], "elements": [], "visible_text": ""})
    result = w.TypeInWindow(desktop).run({**args(), "text": "hi", "find_text": "Message"})
    assert result["ok"] and desktop.clicks == [(15, 25)] and desktop.typed == ["hi"]


def test_type_by_find_text_refuses_and_types_nothing_when_nothing_matches():
    desktop = ScreenDesktop()
    desktop.screens.append({"found": [], "elements": [], "visible_text": ""})
    result = w.TypeInWindow(desktop).run({**args(), "text": "hi", "find_text": "Nonexistent"})
    assert not result["ok"] and desktop.clicks == [] and desktop.typed == []


def test_type_still_accepts_raw_click_coordinates_without_find_text():
    desktop = FakeDesktop()
    result = w.TypeInWindow(desktop).run({**args(), "text": "hi", "click_x": 3, "click_y": 4})
    assert result["ok"] and desktop.clicks == [(3, 4)] and desktop.typed == ["hi"]


# ---- generalized window matching: must work for any app, not a fixed list ----
def test_match_window_works_for_arbitrary_apps_not_a_fixed_list():
    windows = [
        w.Window(1, 10, "Untitled - Notepad", 600, 400, "notepad.exe"),
        w.Window(2, 20, "Mozilla Firefox", 1000, 700, "firefox.exe"),
        w.Window(3, 30, "Discord", 900, 600, "discord.exe"),
        w.Window(4, 40, "Inbox - Outlook", 1100, 800, "outlook.exe"),
    ]
    assert w.match_window("notepad", windows).process_name == "notepad.exe"
    assert w.match_window("firefox", windows).process_name == "firefox.exe"
    assert w.match_window("discord", windows).process_name == "discord.exe"
    assert w.match_window("outlook", windows).title == "Inbox - Outlook"
    assert w.match_window("some app that is not open", windows) is None
    assert w.match_window("x", []) is None


def test_desktop_window_search_delegates_to_the_general_matcher(monkeypatch):
    windows = [w.Window(5, 50, "Slack | My Workspace", 800, 600, "slack.exe")]
    desktop = w.Win32Desktop.__new__(w.Win32Desktop)  # skip __init__, no real Win32 calls needed
    monkeypatch.setattr(desktop, "list_windows", lambda: windows, raising=False)
    found = w.Win32Desktop.find_by_title_or_process(desktop, "slack")
    assert found is not None and found.process_name == "slack.exe"


# ---- the bugs that made the real Win32 layer fail ----
@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_input_struct_matches_what_windows_requires():
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(w._Input) == expected   # a keyboard-only union is 32 bytes and SendInput rejects it


def test_module_has_the_names_the_win32_layer_uses():
    assert hasattr(w, "time") and hasattr(w.Win32Desktop, "_declare_signatures")
    if os.name == "nt":
        desktop = w.Win32Desktop()
        assert desktop.kernel32 is not None      # was never defined before


# ---- typed text must be plain ----
@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\tb", "x\x1b[2J", "line two", "ok\x00"])
def test_control_characters_are_rejected_and_nothing_is_sent(bad):
    desktop = FakeDesktop()
    result = w.TypeInWindow(desktop).run(args(text=bad))
    assert not result["ok"] and "control characters" in result["error"] and desktop.typed == []


def test_ordinary_unicode_text_is_allowed():
    desktop = FakeDesktop()
    assert w.TypeInWindow(desktop).run(args(text="Hello, wörld — 你好 🙂"))["ok"]
    assert desktop.typed == ["Hello, wörld — 你好 🙂"]


# ---- command shells and system dialogs are off limits ----
@pytest.mark.parametrize("proc", sorted(w.BLOCKED_PROCESSES))
def test_input_into_command_shells_and_system_dialogs_is_refused(proc):
    desktop = FakeDesktop(process_name=proc)
    for tool, extra in ((w.TypeInWindow, {"text": "dir"}), (w.ClickInWindow, {"x": 1, "y": 1}),
                        (w.PressKeyInWindow, {"key": "enter", "modifiers": []})):
        result = tool(desktop).run(args(**extra))
        assert not result["ok"] and "not allowed" in result["error"]
    assert desktop.typed == [] and desktop.pressed == [] and desktop.clicks == []


def test_inspecting_a_blocked_window_is_still_possible_so_jarvis_can_explain():
    desktop = FakeDesktop(process_name="powershell.exe", title="Windows PowerShell")
    assert w.InspectActiveWindow(desktop).run({})["ok"]


# ---- shortcuts that leave the app ----
@pytest.mark.parametrize("key,mods", [("tab", ["alt"]), ("tab", ["alt", "shift"]), ("escape", ["alt"]),
                                       ("escape", ["ctrl"]), ("escape", ["ctrl", "shift"]), ("delete", ["ctrl", "alt"])])
def test_system_level_shortcuts_are_refused(key, mods):
    desktop = FakeDesktop()
    result = w.PressKeyInWindow(desktop).run(args(key=key, modifiers=mods))
    assert not result["ok"] and desktop.pressed == []


@pytest.mark.parametrize("key,mods,expected", [("enter", [], (0x0D, [])), ("n", ["ctrl"], (ord("N"), [0x11])),
                                                ("n", ["ctrl", "shift"], (ord("N"), [0x11, 0x10]))])
def test_ordinary_app_keys_and_shortcuts_work(key, mods, expected):
    desktop = FakeDesktop()
    assert w.PressKeyInWindow(desktop).run(args(key=key, modifiers=mods))["ok"]
    assert desktop.pressed == [expected]


@pytest.mark.parametrize("mods", [["win"], ["meta"], ["ctrl", "ctrl"]])
def test_windows_key_and_bad_modifiers_are_refused(mods):
    desktop = FakeDesktop()
    assert not w.PressKeyInWindow(desktop).run(args(key="r", modifiers=mods))["ok"]


# ---- approval rules: always asked, never pre-approvable ----
class Approver:
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    async def approve(self, request):
        self.asked.append(request.tool)
        return self.answer


class Audit:
    def __init__(self):
        self.events = []

    def record(self, kind, **data):
        self.events.append((kind, data))


def test_every_ui_tool_asks_every_time_even_with_every_grant_and_access_mode():
    names = {"inspect_active_window", "click_in_window", "type_in_window", "press_key_in_window"}
    assert not (names & GRANTABLE)
    desktop = FakeDesktop()
    reg = ToolRegistry([w.InspectActiveWindow(desktop), w.ClickInWindow(desktop), w.TypeInWindow(desktop),
                        w.PressKeyInWindow(desktop)])
    for mode in (1, 2):
        policy = SessionPolicy(mode)
        policy.set_grants(list(GRANTABLE) + list(names))       # try to grant them anyway
        assert policy.grants == GRANTABLE & policy.grants and not (names & policy.grants)
        approver = Approver(True)
        ex = ToolExecutor(reg, policy, approver, Audit())
        asyncio.run(ex.execute("inspect_active_window", {}))
        asyncio.run(ex.execute("click_in_window", args(x=5, y=5)))
        asyncio.run(ex.execute("type_in_window", args(text="hi")))
        asyncio.run(ex.execute("press_key_in_window", args(key="enter", modifiers=[])))
        assert approver.asked == sorted(names, key=["inspect_active_window", "click_in_window", "type_in_window",
                                                     "press_key_in_window"].index)


def test_denied_ui_action_sends_nothing():
    desktop = FakeDesktop()
    reg = ToolRegistry([w.TypeInWindow(desktop), w.PressKeyInWindow(desktop)])
    ex = ToolExecutor(reg, SessionPolicy(2), Approver(False), Audit())
    assert not asyncio.run(ex.execute("type_in_window", args(text="hi")))["ok"]
    assert not asyncio.run(ex.execute("press_key_in_window", args(key="enter", modifiers=[])))["ok"]
    assert desktop.typed == [] and desktop.pressed == []


def test_typed_text_is_not_written_to_the_audit_log():
    desktop, audit = FakeDesktop(), Audit()
    ex = ToolExecutor(ToolRegistry([w.TypeInWindow(desktop)]), SessionPolicy(1), Approver(True), audit)
    asyncio.run(ex.execute("type_in_window", args(text="my secret sentence")))
    logged = str(audit.events)
    assert "my secret sentence" not in logged and "text_length" in logged


# ---- the default and the off switch ----
def test_windows_ui_is_on_by_default_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("JARVIS_WINDOWS_UI_CONTROL", raising=False)
    assert Settings.from_env().windows_ui_control is True
    for off in ("0", "false", "No", "OFF"):
        monkeypatch.setenv("JARVIS_WINDOWS_UI_CONTROL", off)
        assert Settings.from_env().windows_ui_control is False
    monkeypatch.setenv("JARVIS_WINDOWS_UI_CONTROL", "1")
    assert Settings.from_env().windows_ui_control is True
