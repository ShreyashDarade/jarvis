import asyncio

from jarvis_ai.application.permissions import PermissionsController
from jarvis_ai.application.tool_executor import ToolExecutor
from jarvis_ai.domain.ports import SandboxResult
from jarvis_ai.policy.approval import GRANTABLE, SessionPolicy
from jarvis_ai.tools.apps import AppEntry, OpenApp, StartMenuIndex
from jarvis_ai.tools.code import CreateTool, RunCode
from jarvis_ai.tools.registry import ToolRegistry
from jarvis_ai.tools.windows_ui import ClickInWindow, FocusWindow, InspectActiveWindow, PressKeyInWindow, ReadScreen, TypeInWindow, Window



class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, e):
        self.events.append(e)


class Audit:
    def __init__(self):
        self.events = []

    def record(self, kind, **d):
        self.events.append((kind, d))


class Sandbox:
    def run(self, code, stdin="", timeout=None):
        return SandboxResult(0, "ok", "")


class Approver:
    def __init__(self):
        self.asked = []

    async def approve(self, request):
        self.asked.append(request.tool)
        return True


def tools():
    reg = ToolRegistry([OpenApp(StartMenuIndex(loader=lambda: [AppEntry("Notepad", "n")]), launch=lambda a: None),
                        RunCode(Sandbox())])
    reg.register(CreateTool(reg, Sandbox()))
    return reg


def test_granted_tool_skips_prompt_but_others_still_ask():
    reg, policy = tools(), SessionPolicy(1)
    policy.set_grants(["open_app"])
    approver = Approver()
    ex = ToolExecutor(reg, policy, approver, Audit())
    assert asyncio.run(ex.execute("open_app", {"app": "notepad"}))["ok"]
    assert approver.asked == []                      # pre-approved
    asyncio.run(ex.execute("run_code", {"code": "print(1)"}))
    assert approver.asked == ["run_code"]            # not granted -> still asks


def test_code_execution_can_never_be_granted():
    reg, policy = tools(), SessionPolicy(1)
    granted = policy.set_grants(["run_code", "create_tool", "delegate_task", "open_app", "nonsense"])
    assert granted == {"open_app"}
    assert not ({"run_code", "create_tool", "delegate_task"} & GRANTABLE)
    asyncio.run(ToolExecutor(reg, policy, Approver(), Audit()).execute(
        "create_tool", {"name": "my_tool", "description": "", "parameters": {"type": "object"}, "code": "x"}))
    # a dynamic tool created mid-session is never covered by a grant, even if its name were listed
    policy.set_grants(["my_tool"])
    assert policy.requires_approval(reg.get("my_tool"))


def test_controller_publishes_expires_and_revokes():
    async def scenario():
        sink, audit, policy = Sink(), Audit(), SessionPolicy(1)
        pc = PermissionsController(policy, sink, audit, ttl=0.1)
        await pc.set_grants(["open_app", "read_file"])
        assert sink.events[-1]["granted"] == ["open_app", "read_file"] and sink.events[-1]["expires_in"] == 0.1
        await asyncio.sleep(0.25)                    # grant expires on its own
        assert policy.grants == frozenset() and sink.events[-1]["reason"] == "expired"
        await pc.set_grants(["open_app"])
        await pc.set_grants([])                      # revoke
        assert policy.grants == frozenset() and sink.events[-1]["expires_in"] is None
        assert [k for k, _ in audit.events if k == "grants_change"]  # every change is audited

    asyncio.run(scenario())


def test_mode_change_keeps_grants_and_unknown_mode_is_strict():
    policy = SessionPolicy(2)
    policy.set_grants(["open_app"])
    policy.set_mode(99)
    assert policy.mode == 1 and policy.grants == {"open_app"}


class Desktop:
    def __init__(self):
        self.window = Window(0x1234, 88, "Notes", 600, 400, "notepad.exe")
        self.active = self.window
        self.clicks, self.typed, self.pressed = [], [], []

    def foreground(self):
        return self.active

    def list_windows(self):
        return [self.window]

    def find_by_title_or_process(self, name: str):
        n = name.lower()
        if n in {"cloud code", "clock code", "claude code", "claude", "cloud"}:
            return self.window
        return self.window if n in self.window.title.lower() or n in self.window.process_name.lower() else None

    def find(self, window_id):
        return self.window if window_id == self.window.window_id else None

    def activate(self, window):
        self.active = window
        return True

    def click(self, window, x, y):
        self.clicks.append((window, x, y))

    def type_text(self, window, text):
        self.typed.append((window, text))

    def press_key(self, window, key_code, modifier_codes):
        self.pressed.append((window, key_code, modifier_codes))

    def read_screen(self, window, find_text=""):
        elem = {"text": "New Chat", "x": 10, "y": 20, "width": 80, "height": 30, "click_x": 50, "click_y": 35}
        return {"elements": [elem], "found": [elem] if not find_text or "new" in find_text.lower() else [], "visible_text": "New Chat"}



def test_windows_ui_actions_always_ask_are_bound_to_one_inspected_window_and_redact_text():
    desktop, audit, approver = Desktop(), Audit(), Approver()
    policy = SessionPolicy(2)
    policy.set_grants(["open_app"])
    inspect, click, typer = InspectActiveWindow(desktop), ClickInWindow(desktop), TypeInWindow(desktop)
    reg = ToolRegistry([inspect, click, typer])
    ex = ToolExecutor(reg, policy, approver, audit)
    snapshot = asyncio.run(ex.execute("inspect_active_window", {}))
    assert snapshot["window"]["id"] == "1234:88"
    args = {"window_id": "1234:88", "window_title": "Notes", "x": 25, "y": 30}
    assert asyncio.run(ex.execute("click_in_window", args))["ok"]
    assert desktop.clicks[-1][1:] == (25, 30)
    assert asyncio.run(ex.execute("type_in_window", {"window_id": "1234:88", "window_title": "Notes", "text": "secret"}))["ok"]
    assert approver.asked == ["inspect_active_window", "click_in_window", "type_in_window"]
    typed_audit = [event for event in audit.events if event[1].get("tool") == "type_in_window"][0]
    assert typed_audit[1]["args"] == {"window_id": "1234:88", "window_title": "Notes", "text_length": 6}


def test_windows_ui_rejects_outside_coordinates_and_changed_title_without_input():
    desktop = Desktop()
    click = ClickInWindow(desktop)
    assert not click.run({"window_id": "1234:88", "window_title": "Notes", "x": 600, "y": 0})["ok"]
    desktop.window = Window(0x1234, 88, "Different document", 600, 400)
    result = TypeInWindow(desktop).run({"window_id": "1234:88", "window_title": "Notes", "text": "no"})
    assert not result["ok"] and desktop.typed == []


def test_windows_ui_can_submit_with_enter_or_use_an_approved_app_shortcut():
    desktop = Desktop()
    enter = PressKeyInWindow(desktop).run({"window_id": "1234:88", "window_title": "Notes", "key": "enter", "modifiers": []})
    shortcut = PressKeyInWindow(desktop).run({"window_id": "1234:88", "window_title": "Notes", "key": "n", "modifiers": ["ctrl"]})
    assert enter["ok"] and shortcut["ok"]
    assert desktop.pressed[0][1:] == (0x0D, [])
    assert desktop.pressed[1][1:] == (ord("N"), [0x11])
    assert not PressKeyInWindow(desktop).run({"window_id": "1234:88", "window_title": "Notes", "key": "r", "modifiers": ["win"]})["ok"]


def test_read_screen_finds_elements_and_buttons():
    from jarvis_ai.tools.windows_ui import ReadScreen
    desktop = Desktop()
    reader = ReadScreen(desktop)
    res = reader.run({"window_id": "1234:88", "window_title": "Notes", "find_text": "New"})
    assert res["ok"]
    assert len(res["found"]) == 1
    assert res["found"][0]["click_x"] == 50


def test_shell_command_and_system_files(tmp_path):
    from jarvis_ai.tools.shell import RunShellCommand
    from jarvis_ai.tools.system_files import ReadSystemFile, WriteSystemFile
    shell = RunShellCommand()
    res = shell.run({"command": "Write-Output 'hello shell'"})
    assert res["ok"] and "hello shell" in res["stdout"]

    test_file = tmp_path / "test_out.txt"
    writer = WriteSystemFile()
    assert writer.run({"path": str(test_file), "content": "outside workspace"})["ok"]
    reader = ReadSystemFile()
    read_res = reader.run({"path": str(test_file)})
    assert read_res["ok"] and read_res["content"] == "outside workspace"


def test_mode_3_autonomous_auto_approves_all_tools_in_one_flow():
    desktop, approver, audit = Desktop(), Approver(), Audit()
    # Mode 3: Autonomous Mode per signed agreement
    policy = SessionPolicy(3)
    inspect = InspectActiveWindow(desktop)
    click = ClickInWindow(desktop)
    typer = TypeInWindow(desktop)
    presser = PressKeyInWindow(desktop)
    reader = ReadScreen(desktop)
    reg = ToolRegistry([inspect, click, typer, presser, reader])
    ex = ToolExecutor(reg, policy, approver, audit)

    # In Mode 3, every UI tool executes without triggering an approval card
    res_inspect = asyncio.run(ex.execute("inspect_active_window", {}))
    assert res_inspect["ok"]
    wid = res_inspect["window"]["id"]
    title = res_inspect["window"]["title"]

    res_click = asyncio.run(ex.execute("click_in_window", {"window_id": wid, "window_title": title, "x": 10, "y": 10}))
    assert res_click["ok"]

    res_type = asyncio.run(ex.execute("type_in_window", {"window_id": wid, "window_title": title, "text": "hello", "press_enter": True}))
    assert res_type["ok"] and res_type["typed"]["submitted"] is True

    res_read = asyncio.run(ex.execute("read_screen", {"window_id": wid, "window_title": title}))
    assert res_read["ok"]

    # Approver was never prompted at all because Mode 3 auto-approves everything in one continuous flow!
    assert approver.asked == []


def test_focus_window_finds_a_stubbed_window_by_name():
    from jarvis_ai.tools.windows_ui import FocusWindow
    desktop = Desktop()
    focus = FocusWindow(desktop)
    res = focus.run({"app_name": "notepad"})
    assert res["ok"] and res["window"]["id"] == "1234:88"


def test_app_name_matching_tolerates_mishearings_generically_not_just_one_app():
    # LAUNCH_THRESHOLD (0.7, apps.py) is what actually matters: this is the bar open_app uses to
    # launch directly rather than asking the user to disambiguate. The mechanism is a generic
    # consonant-skeleton comparison, not a per-app list, so it must hold for any app name.
    from jarvis_ai.tools.apps import LAUNCH_THRESHOLD, score
    for heard, real in [
        ("cloud code application", "Claude"), ("clock code application", "Claude"),
        ("claude code", "Claude"), ("cloud", "Claude"),
        ("curser", "Cursor"), ("anti gravity", "Antigravity"), ("auntie gravity", "Antigravity"),
        ("vs code", "Visual Studio Code"),
    ]:
        assert score(heard, real) >= LAUNCH_THRESHOLD, (heard, real, score(heard, real))
    # And it must not blur unrelated apps together.
    for heard, wrong in [("cloud code", "Notepad"), ("cursor", "Discord"), ("antigravity", "Outlook")]:
        assert score(heard, wrong) < LAUNCH_THRESHOLD, (heard, wrong, score(heard, wrong))


