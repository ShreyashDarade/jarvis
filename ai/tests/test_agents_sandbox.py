import asyncio
import json
import subprocess

import pytest

from jarvis_ai.agents.catalog import AgentCatalog, AgentSpec
from jarvis_ai.agents.runner import AgentRunner
from jarvis_ai.application.tool_executor import ToolExecutor
from jarvis_ai.domain.ports import SandboxResult, SandboxUnavailable
from jarvis_ai.policy.approval import AskBeforeChanges
from jarvis_ai.sandbox.docker_sandbox import DockerSandbox
from jarvis_ai.tools.code import CreateTool, DynamicTool, RunCode
from jarvis_ai.tools.delegate import DelegateTask
from jarvis_ai.tools.files import ReadFile
from jarvis_ai.tools.registry import ToolRegistry
from jarvis_ai.tools.workspace import Workspace


def run(coro):
    return asyncio.run(coro)


class FakeSandbox:
    def __init__(self, result=SandboxResult(0, "42\n", "")):
        self.result, self.calls = result, []

    def run(self, code, stdin="", timeout=None):
        self.calls.append((code, stdin))
        return self.result


class DownSandbox:
    def run(self, code, stdin="", timeout=None):
        raise SandboxUnavailable("Docker is not available, so code execution is disabled")  # real adapter's message


class Approver:
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    async def approve(self, request):
        self.asked.append(request.tool)
        return self.answer


class Audit:
    def record(self, kind, **data):
        pass


# ---- docker sandbox: command line and fail-closed behaviour ----
def fake_docker(returncode=0, stdout="", info_ok=True, timeout=False):
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        if "info" in argv:
            return subprocess.CompletedProcess(argv, 0 if info_ok else 1)
        if "kill" in argv:
            return subprocess.CompletedProcess(argv, 0)
        if timeout:
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    return runner, calls


def test_sandbox_isolation_flags(tmp_path):
    runner, calls = fake_docker(stdout="ok")
    sbx = DockerSandbox(workspace=tmp_path, runner=runner)
    assert sbx.run("print(1)").stdout == "ok"
    argv = calls[-1]
    joined = " ".join(argv)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "no-new-privileges",
                 "--user 65534:65534", "--memory 256m", "--pids-limit 64", "--rm", "--label jarvis=sandbox"):
        assert flag in joined, flag
    assert "readonly" in joined and "target=/sandbox" in joined and "target=/data" in joined
    assert argv[-3:] == ["python", "-I", "/sandbox/main.py"]
    assert "--privileged" not in argv and "-v" not in argv


def test_sandbox_fails_closed_without_docker():
    runner, calls = fake_docker(info_ok=False)
    with pytest.raises(SandboxUnavailable):
        DockerSandbox(runner=runner).run("print(1)")
    assert all("info" in c for c in calls)  # never tried to run anything


def test_sandbox_missing_binary_is_unavailable():
    def runner(argv, **kw):
        raise FileNotFoundError("docker")

    assert not DockerSandbox(runner=runner).available()


def test_sandbox_timeout_kills_container():
    runner, calls = fake_docker(timeout=True)
    result = DockerSandbox(runner=runner).run("while True: pass", timeout=1)
    assert result.timed_out and "kill" in calls[-1]


# ---- code tools ----
def test_run_code_reports_and_fails_closed():
    sbx = FakeSandbox()
    assert RunCode(sbx).run({"code": "print(42)"})["stdout"] == "42\n"
    assert not RunCode(sbx).run({"code": ""})["ok"]
    result = RunCode(DownSandbox()).run({"code": "print(1)"})
    assert not result["ok"] and "disabled" in result["error"]


def test_create_tool_registers_dynamic_tool_that_runs_in_sandbox():
    sbx, reg = FakeSandbox(), ToolRegistry()
    out = CreateTool(reg, sbx).run({"name": "add_numbers", "description": "adds",
                                    "parameters": {"type": "object"}, "code": "import sys,json;..."})
    assert out == {"ok": True, "created": "add_numbers"}
    tool = reg.get("add_numbers")
    assert isinstance(tool, DynamicTool) and reg.dynamic_names() == ["add_numbers"]
    tool.run({"a": 1, "b": 2})
    assert json.loads(sbx.calls[-1][1]) == {"a": 1, "b": 2}  # args go in as JSON on stdin


def test_create_tool_validation(tmp_path):
    reg = ToolRegistry([ReadFile(Workspace(tmp_path))])
    ct = CreateTool(reg, FakeSandbox())
    ok_schema = {"type": "object"}
    assert not ct.run({"name": "Bad Name", "parameters": ok_schema, "code": "x"})["ok"]
    assert not ct.run({"name": "read_file", "description": "", "parameters": ok_schema, "code": "x"})["ok"]  # no shadowing
    assert not ct.run({"name": "good_name", "parameters": {"type": "string"}, "code": "x"})["ok"]
    assert not ct.run({"name": "good_name", "parameters": ok_schema, "code": ""})["ok"]
    assert reg.dynamic_names() == []


def test_registry_schema_scoping():
    sbx, reg = FakeSandbox(), ToolRegistry()
    reg.register(RunCode(sbx))
    CreateTool(reg, sbx).run({"name": "my_tool", "description": "", "parameters": {"type": "object"}, "code": "x"})
    names = lambda s: {x["name"] for x in s}
    assert names(reg.schemas()) == {"run_code"}                      # default view hides dynamic tools
    assert names(reg.schemas(["run_code"], include_dynamic=True)) == {"run_code", "my_tool"}
    assert names(reg.schemas(["run_code"])) == {"run_code"}


# ---- agents ----
class ScriptedModel:
    """Plays back canned Responses-API outputs and records what it was asked."""

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    async def create(self, model, instructions, input, tools):
        self.calls.append({"model": model, "tools": [t["name"] for t in tools], "input_len": len(input)})
        return {"output": self.outputs.pop(0)}


def fn_call(name, args, call_id="c1"):
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args)}


def message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def wire(model, approver, sandbox=None):
    sandbox = sandbox or FakeSandbox()
    reg = ToolRegistry([RunCode(sandbox)])
    reg.register(CreateTool(reg, sandbox))
    executor = ToolExecutor(reg, AskBeforeChanges(), approver, Audit())
    runner = AgentRunner(model, executor, reg, Audit())
    catalog = AgentCatalog(
        [AgentSpec("coder", "write code", "gpt-6.1-sol", ("run_code", "create_tool"), include_dynamic=True)],
        adhoc_model="gpt-5.5", adhoc_tools=("read_file",))
    reg.register(DelegateTask(catalog, runner))
    return reg, executor, catalog


def test_coder_agent_uses_code_model_runs_code_and_returns_answer():
    model = ScriptedModel([[fn_call("run_code", {"code": "print(42)"})], [message("The answer is 42")]])
    approver = Approver(True)
    reg, executor, _ = wire(model, approver)
    result = run(executor.execute("delegate_task", {"role": "coder", "task": "compute"}))
    assert result["ok"] and result["answer"] == "The answer is 42"
    assert result["model"] == "gpt-6.1-sol" and {c["model"] for c in model.calls} == {"gpt-6.1-sol"}
    assert approver.asked == ["run_code"]  # delegation itself is read-only; the code run needed approval


def test_denied_code_never_runs():
    sbx = FakeSandbox()
    model = ScriptedModel([[fn_call("run_code", {"code": "print(1)"})], [message("ok, not run")]])
    reg, executor, _ = wire(model, Approver(False), sbx)
    run(executor.execute("delegate_task", {"role": "coder", "task": "x"}))
    assert sbx.calls == []


def test_agent_cannot_call_tools_outside_its_spec():
    sbx = FakeSandbox()
    model = ScriptedModel([[fn_call("write_file", {"path": "a", "content": "b"})], [message("done")]])
    reg, executor, _ = wire(model, Approver(True), sbx)
    run(executor.execute("delegate_task", {"role": "coder", "task": "x"}))
    assert "delegate_task" not in model.calls[0]["tools"]  # no recursion


def test_dynamic_tool_becomes_available_in_the_same_run():
    model = ScriptedModel([
        [fn_call("create_tool", {"name": "double_it", "description": "d", "parameters": {"type": "object"},
                                 "code": "print(2)"})],
        [fn_call("double_it", {"n": 2})],
        [message("used it")],
    ])
    reg, executor, _ = wire(model, Approver(True))
    result = run(executor.execute("delegate_task", {"role": "coder", "task": "x"}))
    assert result["answer"] == "used it"
    assert "double_it" not in model.calls[0]["tools"] and "double_it" in model.calls[1]["tools"]


def test_adhoc_agent_needs_instructions_and_gets_read_only_tools():
    reg, executor, catalog = wire(ScriptedModel([[message("hi")]]), Approver(True))
    assert not run(executor.execute("delegate_task", {"role": "poet", "task": "x"}))["ok"]
    spec = catalog.resolve("poet", "You write haiku.")
    assert spec.model == "gpt-5.5" and spec.tool_names == ("read_file",) and not spec.include_dynamic


def test_step_limit_stops_runaway_agent():
    model = ScriptedModel([[fn_call("run_code", {"code": "print(1)"})] for _ in range(20)])
    reg, executor, _ = wire(model, Approver(True))
    result = run(executor.execute("delegate_task", {"role": "coder", "task": "x"}))
    assert "step limit" in result["answer"] and len(model.calls) == 8


def test_sandbox_ignores_a_stray_docker_host_and_picks_a_working_context(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://localhost:2375")
    monkeypatch.delenv("JARVIS_DOCKER_CONTEXT", raising=False)
    seen = []

    def runner(argv, **kw):
        seen.append((argv, kw.get("env")))
        broken = "--context" not in argv            # plain `docker` would use the dead DOCKER_HOST
        return subprocess.CompletedProcess(argv, 1 if broken else 0, "ok", "")

    sbx = DockerSandbox(runner=runner)
    assert sbx.available()
    assert sbx.run("print(1)").stdout == "ok"
    run_argv, run_env = seen[-1]
    assert run_argv[1:3] == ["--context", "desktop-linux"] and run_argv[0].lower().endswith("docker.exe") or run_argv[0] == "docker"
    assert run_env is not None and "DOCKER_HOST" not in run_env


def test_sandbox_context_can_be_forced(monkeypatch):
    monkeypatch.setenv("JARVIS_DOCKER_CONTEXT", "my-ctx")
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert DockerSandbox(runner=runner).available()
    assert calls[0][1:] == ["--context", "my-ctx", "info"]


def test_find_docker_falls_back_to_the_standard_install_location(monkeypatch):
    from jarvis_ai.sandbox import docker_sandbox as ds
    monkeypatch.setattr(ds.shutil, "which", lambda name: None)
    monkeypatch.setattr(ds.os.path, "exists", lambda p: p == ds.WINDOWS_DOCKER)
    assert ds.find_docker() == ds.WINDOWS_DOCKER
    monkeypatch.setattr(ds.os.path, "exists", lambda p: False)
    assert ds.find_docker() == "docker"
