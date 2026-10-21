"""System-wide file reading and writing tools with approval."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..domain.ports import Risk

MAX_OUTPUT = 8000


class ReadSystemFile:
    name = "read_system_file"
    description = (
        "Read a text file from any path on the Windows system (e.g. C:\\path\\to\\file). "
        "Allows reaching files outside the workspace folder. Always asks for approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Full path to the file to read."}
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    risk = Risk.READ
    always_approve = True

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = str(args.get("path", "")).strip()
        if not raw:
            return {"ok": False, "error": "path must not be empty"}
        p = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        if not p.is_file():
            return {"ok": False, "error": f"file not found: {p}"}
        try:
            return {"ok": True, "content": p.read_text(encoding="utf-8")[:MAX_OUTPUT],
                    "summary": f"Read `{p.name}`"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class WriteSystemFile:
    name = "write_system_file"
    description = (
        "Write a text file to any path on the Windows system. "
        "Allows reaching files outside the workspace folder. Always asks for approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Full path to the file to write."},
            "content": {"type": "string", "description": "The text content to write."}
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    risk = Risk.CHANGE
    always_approve = True

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = str(args.get("path", "")).strip()
        if not raw:
            return {"ok": False, "error": "path must not be empty"}
        p = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        content = str(args.get("content", ""))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"ok": True, "bytes": len(content), "path": str(p),
                    "summary": f"Wrote {len(content)} bytes to `{p.name}`"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
