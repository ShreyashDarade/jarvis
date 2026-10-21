"""Tools that execute model-written code. Everything here goes through the Sandbox port."""
import json
import re
from typing import Any

from ..domain.ports import Risk, Sandbox, SandboxResult, SandboxUnavailable
from .registry import ToolRegistry

MAX_CODE = 20_000
MAX_DYNAMIC_TOOLS = 20
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def _report(result: SandboxResult) -> dict[str, Any]:
    return {"ok": result.exit_code == 0, "exit_code": result.exit_code, "stdout": result.stdout,
            "stderr": result.stderr, "timed_out": result.timed_out}


def _run_in_sandbox(sandbox: Sandbox, code: str, stdin: str = "") -> dict[str, Any]:
    try:
        return _report(sandbox.run(code, stdin))
    except SandboxUnavailable as e:
        return {"ok": False, "error": str(e)}


class RunCode:
    name = "run_code"
    description = ("Run Python 3 code in an isolated sandbox: no network, no access to the host except the "
                   "workspace folder, read-only at /data. Print results to stdout. Standard library only.")
    parameters = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}
    risk = Risk.CHANGE

    def __init__(self, sandbox: Sandbox):
        self._sandbox = sandbox

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        code = str(args.get("code", ""))
        if not code or len(code) > MAX_CODE:
            return {"ok": False, "error": f"code must be 1..{MAX_CODE} characters"}
        return _run_in_sandbox(self._sandbox, code)


class DynamicTool:
    """A tool defined at runtime. Each call runs its code in the sandbox with the arguments as JSON on stdin."""
    risk = Risk.CHANGE
    dynamic = True

    def __init__(self, name: str, description: str, parameters: dict[str, Any], code: str, sandbox: Sandbox):
        self.name, self.description, self.parameters = name, description, parameters
        self._code, self._sandbox = code, sandbox

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        return _run_in_sandbox(self._sandbox, self._code, json.dumps(args))


class CreateTool:
    name = "create_tool"
    description = ("Define a new tool at runtime. `code` is Python 3 that reads its arguments as one JSON "
                   "object from stdin and prints its result to stdout; it runs in the sandbox on every call. "
                   "`parameters` is a JSON schema of type object.")
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "snake_case, 3-41 chars"},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "code": {"type": "string"},
        },
        "required": ["name", "description", "parameters", "code"],
    }
    risk = Risk.CHANGE

    def __init__(self, registry: ToolRegistry, sandbox: Sandbox):
        self._registry, self._sandbox = registry, sandbox

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        name, code = str(args.get("name", "")), str(args.get("code", ""))
        schema = args.get("parameters")
        if not NAME_RE.match(name):
            return {"ok": False, "error": "name must match ^[a-z][a-z0-9_]{2,40}$"}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            return {"ok": False, "error": "parameters must be a JSON schema with type object"}
        if not code or len(code) > MAX_CODE:
            return {"ok": False, "error": f"code must be 1..{MAX_CODE} characters"}
        if len(self._registry.dynamic_names()) >= MAX_DYNAMIC_TOOLS:
            return {"ok": False, "error": "too many dynamic tools"}
        try:
            self._registry.register(
                DynamicTool(name, str(args.get("description", "")), schema, code, self._sandbox))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "created": name}
