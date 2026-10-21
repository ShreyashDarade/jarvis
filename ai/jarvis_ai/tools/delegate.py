from typing import Any

from ..agents.catalog import AgentCatalog
from ..agents.runner import AgentRunner
from ..domain.ports import Risk


class DelegateTask:
    name = "delegate_task"
    parameters = {
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "An existing role, or a new name for a custom agent."},
            "task": {"type": "string"},
            "instructions": {"type": "string", "description": "Required to create a custom agent for a new role."},
        },
        "required": ["role", "task"],
    }
    risk = Risk.READ  # the agent's own tool calls still go through approval

    def __init__(self, catalog: AgentCatalog, runner: AgentRunner):
        self._catalog, self._runner = catalog, runner
        self.description = (
            f"Hand a task to a specialist agent and get its answer. Roles: {', '.join(catalog.roles())}. "
            "Use 'coder' for anything that needs code, calculation or a new tool. You may also define a "
            "custom agent by giving a new role name plus instructions."
        )

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        spec = self._catalog.resolve(str(args.get("role", "")), str(args.get("instructions", "")))
        if spec is None:
            return {"ok": False, "error": f"unknown role; known: {self._catalog.roles()}"}
        answer = await self._runner.run(spec, str(args.get("task", "")))
        return {"ok": True, "agent": spec.name, "model": spec.model, "answer": answer}
