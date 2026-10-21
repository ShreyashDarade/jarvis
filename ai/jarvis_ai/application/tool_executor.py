import asyncio
import inspect
from typing import Any

from ..domain.ports import ApprovalPolicy, Approver, ApprovalRequest, AuditLog
from ..tools.registry import ToolRegistry


class ToolExecutor:
    """Runs one tool call: look up, ask for approval if the policy says so, run, audit."""

    def __init__(self, registry: ToolRegistry, policy: ApprovalPolicy, approver: Approver, audit: AuditLog):
        self._registry, self._policy, self._approver, self._audit = registry, policy, approver, audit

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self._registry.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool {name}"}

        approved = True
        if self._policy.requires_approval(tool):
            approved = await self._approver.approve(ApprovalRequest(name, args))
        # The approval card receives the complete arguments.  A tool may return
        # a redacted representation for the durable audit log (for example text
        # destined for a password field).
        audit_args = tool.audit_args(args) if hasattr(tool, "audit_args") else args
        self._audit.record("tool_request", tool=name, args=audit_args, approved=approved)
        if not approved:
            return {"ok": False, "error": "denied by user; do not retry"}

        try:
            if inspect.iscoroutinefunction(tool.run):
                result = await tool.run(args)
            else:
                result = await asyncio.to_thread(tool.run, args)
        except Exception as e:  # tool failures go back to the model, they must not crash the session
            self._audit.record("tool_error", tool=name, error=str(e))
            return {"ok": False, "error": str(e)}
        self._audit.record("tool_result", tool=name, result=str(result)[:500])
        return result
