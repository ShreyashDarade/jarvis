"""Composition root: the only place concrete classes are wired together."""
import asyncio
import sys

from .adapters.audit_file import FileAuditLog
from .adapters.client_approver import ClientApprover
from .adapters.openai_live import LiveSettings, OpenAILiveTransport
from .adapters.openai_responses import OpenAIResponsesClient
from .adapters.ws_server import AIServer, Built, WebSocketSink
from .agents.catalog import AgentCatalog, AgentSpec
from .agents.runner import AgentRunner
from .application.session import AssistantSession
from .application.tool_executor import ToolExecutor
from .application.permissions import PermissionsController
from .config import Settings
from .policy.approval import SessionPolicy
from .prompts import ANALYST_INSTRUCTIONS, CODER_INSTRUCTIONS, Capabilities, backend_instructions, live_instructions
from .sandbox.docker_sandbox import DockerSandbox
from .tools.apps import FindApps, OpenApp, StartMenuIndex
from .tools.code import CreateTool, RunCode
from .tools.delegate import DelegateTask
from .tools.files import ListFiles, ReadFile, WriteFile
from .tools.launchers import OpenUrl
from .tools.registry import ToolRegistry
from .tools.shell import RunShellCommand
from .tools.system import SystemInfo
from .tools.system_files import ReadSystemFile, WriteSystemFile
from .tools.workspace import Workspace
from .tools.windows_ui import ClickInWindow, FocusWindow, InspectActiveWindow, PressKeyInWindow, ReadScreen, TypeInWindow, Win32Desktop

# What the voice model may call directly. Code execution is reachable only through the coder agent.
BASE_LIVE_TOOLS = ("open_app", "find_apps", "open_url", "list_files", "read_file", "write_file",
                   "read_system_file", "write_system_file", "run_shell_command", "system_info", "delegate_task")


def main() -> None:
    settings = Settings.from_env()
    if not settings.api_key:
        sys.exit("OPENAI_API_KEY is not set in the environment")
    if not settings.secret:
        sys.exit("JARVIS_AI_SECRET is not set (the Go gateway provides it)")

    audit = FileAuditLog(settings.audit_log)
    workspace = Workspace(settings.workspace)
    sandbox = DockerSandbox(settings.sandbox_image, workspace=workspace.root)
    if sandbox.available():
        print(f"Sandbox: Docker ready ({settings.sandbox_image})", flush=True)
    else:
        print("Sandbox: Docker not available - code execution is DISABLED (it will not run unisolated)", flush=True)

    catalog = AgentCatalog(
        [
            AgentSpec("coder", CODER_INSTRUCTIONS, settings.code_model,
                      ("run_code", "create_tool", "list_files", "read_file", "write_file", "read_system_file", "write_system_file"), include_dynamic=True),
            AgentSpec("analyst", ANALYST_INSTRUCTIONS, settings.backend_model, ("list_files", "read_file", "read_system_file")),
        ],
        adhoc_model=settings.backend_model,
        adhoc_tools=("list_files", "read_file", "read_system_file"),
    )
    models = OpenAIResponsesClient(settings.api_key)

    # Each browser session gets its own registry so dynamic tools never leak between sessions.
    apps = StartMenuIndex()  # shared: the app list is cached for a few minutes
    desktop = None
    if settings.windows_ui_control:
        try:
            desktop = Win32Desktop()
            print("Windows UI control: ON (desktop apps, windows, OCR screen reading and input)", flush=True)
        except RuntimeError as e:
            print(f"Windows UI control: unavailable ({e})", flush=True)
    else:
        print("Windows UI control: off", flush=True)
    live_tools = BASE_LIVE_TOOLS + (("inspect_active_window", "focus_window", "read_screen", "click_in_window", "type_in_window", "press_key_in_window") if desktop else ())

    caps = Capabilities(windows_ui=bool(desktop), code=sandbox.available())

    def make_registry() -> ToolRegistry:
        reg = ToolRegistry([
            OpenApp(apps, desktop=desktop), FindApps(apps), OpenUrl(), SystemInfo(),
            ListFiles(workspace), ReadFile(workspace), WriteFile(workspace),
            ReadSystemFile(), WriteSystemFile(), RunShellCommand(), RunCode(sandbox),
        ])
        if desktop:
            reg.register(InspectActiveWindow(desktop))
            reg.register(FocusWindow(desktop))
            reg.register(ReadScreen(desktop))
            reg.register(ClickInWindow(desktop))
            reg.register(TypeInWindow(desktop))
            reg.register(PressKeyInWindow(desktop))
        reg.register(CreateTool(reg, sandbox))
        return reg


    def build(sink: WebSocketSink) -> Built:
        reg = make_registry()
        approver = ClientApprover(sink)
        policy = SessionPolicy(1)  # the UI's start message sets the real level
        executor = ToolExecutor(reg, policy, approver, audit)
        reg.register(DelegateTask(catalog, AgentRunner(models, executor, reg, audit)))
        transport = OpenAILiveTransport(
            LiveSettings(settings.api_key, settings.backend_model, settings.voice, reg.schemas(live_tools),
                         live_instructions(caps), backend_instructions(caps))
        )
        return Built(AssistantSession(transport, executor, sink, audit), approver,
                     PermissionsController(policy, sink, audit))

    greeting = [{"type": "sandbox", "available": sandbox.available(), "image": settings.sandbox_image},
                {"type": "windows_ui", "available": bool(desktop)}]
    server = AIServer("127.0.0.1", settings.port, settings.secret, build, audit, greeting)
    asyncio.run(server.serve_forever())


if __name__ == "__main__":
    main()
