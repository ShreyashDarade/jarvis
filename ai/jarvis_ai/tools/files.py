from typing import Any

from ..domain.ports import Risk
from .workspace import Workspace

MAX_OUTPUT = 8000


class ListFiles:
    name = "list_files"
    description = "List files in the workspace folder (or a subfolder)."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    risk = Risk.READ

    def __init__(self, workspace: Workspace):
        self._ws = workspace

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        folder = self._ws.resolve(args.get("path", ""))
        return {"ok": True, "entries": sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir())}


class ReadFile:
    name = "read_file"
    description = "Read a text file in the workspace folder."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    risk = Risk.READ

    def __init__(self, workspace: Workspace):
        self._ws = workspace

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = self._ws.resolve(args["path"]).read_text(encoding="utf-8")
        return {"ok": True, "content": text[:MAX_OUTPUT]}


class WriteFile:
    name = "write_file"
    description = "Write a text file in the workspace folder."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }
    risk = Risk.CHANGE

    def __init__(self, workspace: Workspace):
        self._ws = workspace

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._ws.resolve(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return {"ok": True, "bytes": len(args["content"])}
