from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class AgentSpec:
    name: str
    instructions: str
    model: str
    tool_names: tuple[str, ...]
    include_dynamic: bool = False   # may also use tools created at runtime


class AgentCatalog:
    """Known agent roles. Ad-hoc agents are built on demand from a role name plus instructions."""

    def __init__(self, specs: Iterable[AgentSpec], adhoc_model: str, adhoc_tools: tuple[str, ...]):
        self._specs = {s.name: s for s in specs}
        self._adhoc_model, self._adhoc_tools = adhoc_model, adhoc_tools

    def roles(self) -> list[str]:
        return list(self._specs)

    def resolve(self, role: str, instructions: str = "") -> AgentSpec | None:
        if role in self._specs:
            return self._specs[role]
        if instructions.strip():
            # A dynamic agent: model-defined instructions, read-only tools, no code execution.
            return AgentSpec(role[:40], instructions[:4000], self._adhoc_model, self._adhoc_tools)
        return None
