"""Host shell execution tool with mandatory human approval."""
from __future__ import annotations

import subprocess
from typing import Any

from ..domain.ports import Risk

MAX_OUTPUT = 8000


class RunShellCommand:
    name = "run_shell_command"
    description = (
        "Run a shell command (PowerShell / cmd) on the host Windows system. "
        "Every command requires explicit user approval before execution."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The exact shell command line to run."}
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    risk = Risk.CHANGE
    always_approve = True

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = str(args.get("command", "")).strip()
        if not cmd:
            return {"ok": False, "error": "command must not be empty"}
        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout = res.stdout[:MAX_OUTPUT]
            stderr = res.stderr[:MAX_OUTPUT]
            return {
                "ok": True,
                "exit_code": res.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "summary": f"Ran `{cmd[:50]}` (exit {res.returncode})",
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "command timed out after 60 seconds"}
        except Exception as e:
            return {"ok": False, "error": f"command failed: {e}"}
