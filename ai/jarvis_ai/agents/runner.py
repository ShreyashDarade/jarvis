import json
from typing import Any

from ..domain.ports import AuditLog, ModelClient, ToolInvoker
from ..tools.registry import ToolRegistry
from .catalog import AgentSpec

MAX_STEPS = 8
MAX_TOOL_OUTPUT = 8000


def _text(output: list[dict[str, Any]]) -> str:
    parts = []
    for item in output:
        if item.get("type") == "message":
            parts += [c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text"]
    return "".join(parts)


class AgentRunner:
    """Runs one agent to completion: model call, tool calls through the invoker, repeat."""

    def __init__(self, client: ModelClient, invoker: ToolInvoker, registry: ToolRegistry, audit: AuditLog):
        self._client, self._invoker, self._registry, self._audit = client, invoker, registry, audit

    async def run(self, spec: AgentSpec, task: str) -> str:
        self._audit.record("agent_start", agent=spec.name, model=spec.model)
        items: list[dict[str, Any]] = [{"role": "user", "content": task}]
        for _ in range(MAX_STEPS):
            # Re-read the schemas every step so tools created mid-run become available.
            tools = self._registry.schemas(spec.tool_names, include_dynamic=spec.include_dynamic)
            response = await self._client.create(spec.model, spec.instructions, items, tools)
            output = response.get("output", [])
            items += output
            calls = [o for o in output if o.get("type") == "function_call"]
            if not calls:
                self._audit.record("agent_end", agent=spec.name, steps=len(items))
                return _text(output) or "(no answer)"
            for call in calls:
                allowed = {t["name"] for t in tools}
                if call["name"] not in allowed:
                    result: dict[str, Any] = {"ok": False, "error": f"tool {call['name']} not available to this agent"}
                else:
                    try:
                        args = json.loads(call.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = await self._invoker.execute(call["name"], args)
                items.append({"type": "function_call_output", "call_id": call["call_id"],
                              "output": json.dumps(result)[:MAX_TOOL_OUTPUT]})
        self._audit.record("agent_end", agent=spec.name, steps=len(items), stopped="step limit")
        return "Stopped: step limit reached before the task finished."
