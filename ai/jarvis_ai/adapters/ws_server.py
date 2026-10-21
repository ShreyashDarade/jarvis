"""Internal WebSocket the Go gateway talks to. Loopback only, guarded by a shared secret."""
import asyncio
import hmac
import json
from http import HTTPStatus
from typing import Any, Callable, NamedTuple
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import serve

from ..application.session import AssistantSession
from ..application.permissions import PermissionsController
from ..domain.ports import AuditLog
from .client_approver import ClientApprover


class Built(NamedTuple):
    session: AssistantSession
    approver: ClientApprover
    permissions: PermissionsController


# sink -> Built. Supplied by the composition root so this module knows no concrete tools.
SessionBuilder = Callable[["WebSocketSink"], Built]


class WebSocketSink:
    def __init__(self, ws):
        self._ws = ws

    async def emit(self, event: dict[str, Any]) -> None:
        try:
            await self._ws.send(json.dumps(event))
        except websockets.ConnectionClosed:
            pass


class AIServer:
    def __init__(self, host: str, port: int, secret: str, build: SessionBuilder, audit: AuditLog,
                 greeting: list[dict[str, Any]] | None = None):
        if not secret:
            raise ValueError("a shared secret is required")
        self._host, self._port, self._secret, self._build, self._audit = host, port, secret, build, audit
        self._greeting = greeting or []  # environment facts sent to the UI when a session starts

    def _authorize(self, connection, request):
        got = parse_qs(urlparse(request.path).query).get("secret", [""])[0]
        if not hmac.compare_digest(got, self._secret):
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        return None

    async def serve_forever(self) -> None:
        async with serve(self._handle, self._host, self._port, process_request=self._authorize):
            await asyncio.get_running_loop().create_future()

    async def _handle(self, ws) -> None:
        sink = WebSocketSink(ws)
        built: Built | None = None
        upstream: asyncio.Task | None = None

        async def run_upstream() -> None:
            try:
                await built.session.run()
            except Exception as e:
                self._audit.record("session_error", error=str(e))
                await sink.emit({"type": "error", "message": f"upstream: {e}"[:300]})
            finally:
                await sink.emit({"type": "status", "state": "closed"})

        try:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "start" and built is None:
                    built = self._build(sink)
                    await built.permissions.set_mode(int(msg.get("mode", 1)), "start")
                    for event in self._greeting:
                        await sink.emit(event)
                    upstream = asyncio.create_task(run_upstream())
                    await sink.emit({"type": "status", "state": "connecting"})
                elif kind == "audio" and built:
                    await built.session.send_audio(msg["data"])
                elif kind == "approval" and built:
                    built.approver.resolve(msg["id"], bool(msg.get("approved")))
                elif kind == "set_mode" and built:
                    await built.permissions.set_mode(int(msg.get("mode", 1)), "user")
                elif kind == "grant" and built:
                    await built.permissions.set_grants(msg.get("tools") or [], "user")
                elif kind == "stop":
                    break
        except websockets.ConnectionClosed:
            pass
        finally:
            if built:
                built.permissions.cancel()
                built.approver.deny_all()
                await built.session.close()
            if upstream:
                upstream.cancel()
