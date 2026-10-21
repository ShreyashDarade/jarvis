import asyncio

from ..domain.ports import AuditLog, EventSink
from ..policy.approval import SessionPolicy

GRANT_SECONDS = 3600


class PermissionsController:
    """Owns the session's access level and pre-approved capabilities. Grants always expire and can be revoked."""

    def __init__(self, policy: SessionPolicy, sink: EventSink, audit: AuditLog, ttl: float = GRANT_SECONDS):
        self._policy, self._sink, self._audit, self._ttl = policy, sink, audit, ttl
        self._timer: asyncio.Task | None = None

    async def set_mode(self, mode: int, reason: str = "user") -> None:
        self._policy.set_mode(mode)
        self._audit.record("mode_change", mode=self._policy.mode, reason=reason)
        await self._publish(reason)

    async def set_grants(self, names, reason: str = "user") -> None:
        self.cancel()
        granted = self._policy.set_grants(names)
        self._audit.record("grants_change", granted=sorted(granted), reason=reason)
        if granted:
            self._timer = asyncio.create_task(self._expire())
        await self._publish(reason)

    async def _publish(self, reason: str) -> None:
        granted = sorted(self._policy.grants)
        await self._sink.emit({"type": "permissions", "mode": self._policy.mode, "granted": granted,
                               "expires_in": self._ttl if granted else None, "reason": reason})

    async def _expire(self) -> None:
        await asyncio.sleep(self._ttl)
        self._timer = None
        await self.set_grants([], "expired")

    def cancel(self) -> None:
        if self._timer and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None
