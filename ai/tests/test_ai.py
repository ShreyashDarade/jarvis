import asyncio
import json

import pytest

from jarvis_ai.application.session import AssistantSession
from jarvis_ai.application.tool_executor import ToolExecutor
from jarvis_ai.adapters.client_approver import ClientApprover
from jarvis_ai.domain.ports import ApprovalRequest
from jarvis_ai.policy.approval import AskBeforeChanges, AskEveryAction, policy_for_mode
from jarvis_ai.tools.files import ListFiles, ReadFile, WriteFile
from jarvis_ai.tools.apps import AppEntry, FindApps, OpenApp, StartMenuIndex
from jarvis_ai.tools.launchers import OpenUrl
from jarvis_ai.tools.registry import ToolRegistry
from jarvis_ai.tools.workspace import Workspace


# ---- fakes (the ports make these trivial) ----
class FakeAudit:
    def __init__(self):
        self.events = []

    def record(self, kind, **data):
        self.events.append((kind, data))


class FakeSink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class FakeApprover:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def approve(self, request):
        self.asked.append(request)
        return self.answer


class FakeTransport:
    def __init__(self, incoming):
        self.incoming, self.sent, self.opened = incoming, [], False

    async def open(self):
        self.opened = True

    async def send(self, event):
        self.sent.append(event)

    async def events(self):
        for e in self.incoming:
            yield e
        await asyncio.sleep(0.2)  # a live stream stays open while tool calls finish

    async def close(self):
        pass


APPS = [AppEntry("Notepad", "notepad-id"), AppEntry("Claude", "claude-id"), AppEntry("Claude Code Helper", "cch-id"),
        AppEntry("Calculator", "calc-id"), AppEntry("Microsoft Edge", "edge-id"), AppEntry("Microsoft Excel", "excel-id")]


def fake_index():
    return StartMenuIndex(loader=lambda: APPS)


def make_registry(tmp_path, spawned=None, opened=None):
    ws = Workspace(tmp_path)
    return ToolRegistry([
        OpenApp(fake_index(), launch=(spawned.append if spawned is not None else lambda a: None)),
        OpenUrl(opener=(opened.append if opened is not None else lambda u: None)),
        ListFiles(ws), ReadFile(ws), WriteFile(ws),
    ])


# ---- tools ----
def test_workspace_blocks_escape(tmp_path):
    ws = Workspace(tmp_path)
    for bad in ("../x", "C:/Windows/x.txt", "a/../../x"):
        with pytest.raises(ValueError):
            ws.resolve(bad)


def test_file_tools_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    assert WriteFile(ws).run({"path": "a/b.txt", "content": "hi"})["ok"]
    assert ReadFile(ws).run({"path": "a/b.txt"})["content"] == "hi"
    assert ListFiles(ws).run({})["entries"] == ["a/"]


def test_open_app_launches_by_name_and_only_from_the_index():
    launched = []
    app = OpenApp(fake_index(), launch=launched.append)
    assert app.run({"app": "notepad"}) == {"ok": True, "opened": "Notepad", "summary": "Opened Notepad"}
    assert launched == ["notepad-id"]                      # the app id comes from the index, never the model
    assert app.run({"app": "calc"})["opened"] == "Calculator"   # partial / fuzzy names work
    bad = app.run({"app": "C:/Windows/System32/cmd.exe"})
    assert not bad["ok"] and len(launched) == 2              # a path is just a failed name lookup


def test_open_app_reports_ambiguity_instead_of_guessing():
    launched = []
    result = OpenApp(fake_index(), launch=launched.append).run({"app": "microsoft"})
    assert not result["ok"] and set(result["candidates"]) >= {"Microsoft Edge", "Microsoft Excel"}
    assert launched == []


def test_open_app_never_launches_a_weak_match():
    launched = []
    result = OpenApp(fake_index(), launch=launched.append).run({"app": "cloud code"})
    assert not result["ok"] and launched == []


def test_find_apps_helps_with_misheard_names():
    found = FindApps(fake_index()).run({"query": "claud"})["apps"]
    assert found[0] == "Claude"


def test_start_menu_index_is_cached():
    calls = []
    now = [0.0]
    index = StartMenuIndex(loader=lambda: calls.append(1) or APPS, ttl=60, clock=lambda: now[0])
    index.apps(); index.apps()
    assert len(calls) == 1
    now[0] = 61
    index.apps()
    assert len(calls) == 2


def test_open_url_only_allows_http():
    opened = []
    url = OpenUrl(opener=opened.append)
    assert url.run({"url": "https://example.com"})["ok"]
    assert not url.run({"url": "file:///C:/Windows/system.ini"})["ok"] and len(opened) == 1


def test_registry_rejects_duplicates_and_lists_schemas(tmp_path):
    reg = make_registry(tmp_path)
    assert {s["name"] for s in reg.schemas()} == {"open_app", "open_url", "list_files", "read_file", "write_file"}
    with pytest.raises(ValueError):
        reg.register(ListFiles(Workspace(tmp_path)))


# ---- policy ----
def test_policies(tmp_path):
    reg = make_registry(tmp_path)
    reader, writer = reg.get("read_file"), reg.get("write_file")
    assert AskEveryAction().requires_approval(reader)
    assert not AskBeforeChanges().requires_approval(reader)
    assert AskBeforeChanges().requires_approval(writer)
    assert isinstance(policy_for_mode(99), AskEveryAction)  # unknown -> strictest


# ---- executor ----
def run(coro):
    return asyncio.run(coro)


def test_executor_denied_never_runs_tool(tmp_path):
    spawned = []
    audit, approver = FakeAudit(), FakeApprover(False)
    ex = ToolExecutor(make_registry(tmp_path, spawned), AskEveryAction(), approver, audit)
    result = run(ex.execute("open_app", {"app": "notepad"}))
    assert not result["ok"] and spawned == []
    assert audit.events[0][1]["approved"] is False


def test_executor_approved_runs_and_audits(tmp_path):
    spawned = []
    audit, approver = FakeAudit(), FakeApprover(True)
    ex = ToolExecutor(make_registry(tmp_path, spawned), AskBeforeChanges(), approver, audit)
    assert run(ex.execute("open_app", {"app": "notepad"}))["ok"]
    assert spawned == ["notepad-id"]
    assert [k for k, _ in audit.events] == ["tool_request", "tool_result"]


def test_executor_skips_prompt_for_reads_when_policy_allows(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    approver = FakeApprover(False)
    ex = ToolExecutor(make_registry(tmp_path), AskBeforeChanges(), approver, FakeAudit())
    assert run(ex.execute("read_file", {"path": "f.txt"}))["ok"]
    assert approver.asked == []


def test_executor_unknown_tool_and_tool_errors(tmp_path):
    ex = ToolExecutor(make_registry(tmp_path), AskBeforeChanges(), FakeApprover(True), FakeAudit())
    assert not run(ex.execute("run_command", {"command": "dir"}))["ok"]
    assert "outside" in run(ex.execute("read_file", {"path": "../x"}))["error"]


# ---- session ----
def test_session_relays_and_answers_tool_calls(tmp_path):
    call = {"type": "response.event", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "write_file", "call_id": "c1",
                 "arguments": json.dumps({"path": "n.txt", "content": "hello"})}}}
    transport = FakeTransport([
        {"type": "session.started"},
        {"type": "session.output_audio.delta", "delta": "AAAA"},
        {"type": "session.output_transcript.delta", "delta": "Hi"},
        call,
    ])
    sink, audit = FakeSink(), FakeAudit()
    ex = ToolExecutor(make_registry(tmp_path), AskEveryAction(), FakeApprover(True), audit)

    async def scenario():
        s = AssistantSession(transport, ex, sink, audit)
        await s.run()

    run(scenario())
    kinds = [e["type"] for e in sink.events]
    assert kinds[:3] == ["status", "audio", "transcript"] and "tool_result" in kinds
    assert (tmp_path / "n.txt").read_text() == "hello"
    assert transport.sent[0]["item"]["call_id"] == "c1"
    assert transport.sent[-1] == {"type": "response.create"}


# ---- client approver ----
def test_client_approver_resolves_times_out_and_denies_all():
    async def scenario():
        sink = FakeSink()
        approver = ClientApprover(sink, timeout=0.2)
        task = asyncio.create_task(approver.approve(ApprovalRequest("write_file", {})))
        await asyncio.sleep(0.05)
        approver.resolve(sink.events[0]["id"], True)
        assert await task is True
        assert await approver.approve(ApprovalRequest("write_file", {})) is False  # timeout
        task = asyncio.create_task(approver.approve(ApprovalRequest("x", {})))
        await asyncio.sleep(0.05)
        approver.deny_all()
        assert await task is False

    run(scenario())


def test_failed_tool_call_reports_the_reason_to_the_ui(tmp_path):
    call = {"type": "response.event", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "nope", "call_id": "c9", "arguments": "{}"}}}
    sink, audit = FakeSink(), FakeAudit()
    ex = ToolExecutor(make_registry(tmp_path), AskEveryAction(), FakeApprover(True), audit)
    run(AssistantSession(FakeTransport([call]), ex, sink, audit).run())
    result = next(e for e in sink.events if e["type"] == "tool_result")
    assert result["ok"] is False and "unknown tool" in result["error"]
    assert next(e for e in sink.events if e["type"] == "tool_call")["tool"] == "nope"


def test_ws_server_requires_a_secret_and_keeps_greeting():
    import pytest
    from jarvis_ai.adapters.ws_server import AIServer

    with pytest.raises(ValueError):
        AIServer("127.0.0.1", 0, "", lambda s: None, FakeAudit())
    server = AIServer("127.0.0.1", 0, "s3cret", lambda s: None, FakeAudit(), [{"type": "sandbox", "available": True}])
    assert server._greeting == [{"type": "sandbox", "available": True}]


def test_prompts_only_promise_what_is_available():
    from jarvis_ai.prompts import Capabilities, backend_instructions, live_instructions
    off = Capabilities(windows_ui=False, code=False)
    on = Capabilities(windows_ui=True, code=True)
    live_off, back_off = live_instructions(off), backend_instructions(off)
    # Without the tools, the model is told it cannot, and is never told to use them.
    assert "CANNOT" in live_off and "-WindowsUi" in live_off and "Docker" in live_off
    assert "press_key_in_window" not in back_off and "inspect_active_window" not in back_off
    assert "never claim to" in back_off
    # With the tools, they are described.
    live_on, back_on = live_instructions(on), backend_instructions(on)
    assert "press keys in the active window" in live_on
    assert "inspect_active_window" in back_on and "delegate_task" in back_on
    # Honesty rules are always present, with or without tools.
    for text in (live_off, live_on):
        assert "ask before opening it" in text and "exact thing you did" in text
        assert "Never say you will do something that is not on your CAN list" in text


def test_session_start_uses_the_supplied_instructions():
    from jarvis_ai.adapters.openai_live import LiveSettings, build_session_start
    start = build_session_start(LiveSettings("k", "m", "marin", [], "LIVE TEXT", "BACKEND TEXT"))
    assert start["session"]["instructions"] == "LIVE TEXT"
    assert start["session"]["delegation"]["responses"]["instructions"] == "BACKEND TEXT"


def test_failed_open_names_the_app_in_the_ui_summary(tmp_path):
    call = {"type": "response.event", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "open_app", "call_id": "c5", "arguments": '{"app": "notepad"}'}}}
    sink, audit = FakeSink(), FakeAudit()
    ex = ToolExecutor(make_registry(tmp_path), AskBeforeChanges(), FakeApprover(True), audit)
    run(AssistantSession(FakeTransport([call]), ex, sink, audit).run())
    assert next(e for e in sink.events if e["type"] == "tool_result")["summary"] == "Opened Notepad"
