from ..domain.ports import ApprovalPolicy, Risk, Tool

# Capabilities a user may pre-approve for a session. Anything that executes generated code
# (run_code, create_tool, dynamic tools, delegation to a coder) is deliberately not on this list.
GRANTABLE = frozenset({"open_app", "find_apps", "open_url", "list_files", "read_file", "write_file", "system_info"})



class AskEveryAction:
    def requires_approval(self, tool: Tool) -> bool:
        return True


class AskBeforeChanges:
    def requires_approval(self, tool: Tool) -> bool:
        return getattr(tool, "always_approve", False) or tool.risk is Risk.CHANGE


class AutoApproveAll:
    """Mode 3: Unrestricted Autonomous Mode.
    Enabled when the user accepts and signs the Red Disclaimer agreement.
    All actions execute automatically in one flow without approval interruptions."""
    def requires_approval(self, tool: Tool) -> bool:
        return False


POLICIES: dict[int, type] = {1: AskEveryAction, 2: AskBeforeChanges, 3: AutoApproveAll}


def policy_for_mode(mode: int) -> ApprovalPolicy:
    """Unknown modes fall back to the strictest policy."""
    return POLICIES.get(mode, AskEveryAction)()


class SessionPolicy:
    """A base access level plus capabilities the user pre-approved for this session.

    Grants can cover names in GRANTABLE; they are a property of this session object, so they end
    with the session, and the permissions controller expires and revokes them."""

    def __init__(self, mode: int = 1):
        self.grants: frozenset[str] = frozenset()
        self.set_mode(mode)

    def set_mode(self, mode: int) -> None:
        self.mode = mode if mode in POLICIES else 1
        self._base = policy_for_mode(self.mode)

    def set_grants(self, names) -> frozenset[str]:
        self.grants = frozenset(n for n in names if n in GRANTABLE)
        return self.grants

    def requires_approval(self, tool: Tool) -> bool:
        # In Mode 3 (Autonomous Mode), everything executes auto-approved in one flow
        if self.mode == 3:
            return False
        # Pre-approved grants for this session
        if tool.name in self.grants and not getattr(tool, "dynamic", False):
            return False
        if getattr(tool, "always_approve", False):
            return True
        return self._base.requires_approval(tool)

