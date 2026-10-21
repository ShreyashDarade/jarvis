from typing import Any, Iterable

from ..domain.ports import Tool


class ToolRegistry:
    """Add a tool by registering it; nothing else needs to change (open/closed). Safe to grow at runtime."""

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def dynamic_names(self) -> list[str]:
        return [n for n, t in self._tools.items() if getattr(t, "dynamic", False)]

    def schemas(self, names: Iterable[str] | None = None, include_dynamic: bool = False) -> list[dict[str, Any]]:
        """Schemas for `names` (all non-dynamic tools if None), plus every dynamic tool if requested."""
        wanted = list(self._tools) if names is None else list(names)
        if names is None and not include_dynamic:
            wanted = [n for n in wanted if n not in self.dynamic_names()]
        if names is not None and include_dynamic:
            wanted += self.dynamic_names()
        return [
            {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
            for n in dict.fromkeys(wanted) if (t := self._tools.get(n)) is not None
        ]
