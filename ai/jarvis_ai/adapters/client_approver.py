import asyncio
import uuid

from ..domain.ports import ApprovalRequest, EventSink

APPROVAL_TIMEOUT = 120


class ClientApprover:
    """Asks the user through the UI and waits for the answer. No answer means no."""

    def __init__(self, sink: EventSink, timeout: float = APPROVAL_TIMEOUT):
        self._sink, self._timeout = sink, timeout
        self._pending: dict[str, asyncio.Future] = {}

    async def approve(self, request: ApprovalRequest) -> bool:
        approval_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = future
        await self._sink.emit({"type": "approval_request", "id": approval_id,
                               "tool": request.tool, "args": request.args})
        try:
            return await asyncio.wait_for(future, self._timeout)
        except asyncio.TimeoutError:
            await self._sink.emit({"type": "approval_expired", "id": approval_id})
            return False
        finally:
            self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool) -> None:
        future = self._pending.get(approval_id)
        if future and not future.done():
            future.set_result(approved)

    def deny_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result(False)
