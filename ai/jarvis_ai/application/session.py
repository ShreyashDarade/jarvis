"""Orchestrates one conversation: model events in, UI events out, tool calls in between."""
import asyncio
import json
from typing import Any

from ..domain.ports import AuditLog, EventSink, LiveTransport
from .tool_executor import ToolExecutor


class AssistantSession:
    def __init__(self, transport: LiveTransport, executor: ToolExecutor, sink: EventSink, audit: AuditLog):
        self._transport, self._executor, self._sink, self._audit = transport, executor, sink, audit
        self._calls: set[asyncio.Task] = set()

    async def run(self) -> None:
        self._audit.record("session_start")
        await self._transport.open()
        try:
            async for event in self._transport.events():
                await self._handle(event)
        finally:
            for task in self._calls:
                task.cancel()
            self._audit.record("session_end")

    async def send_audio(self, b64: str) -> None:
        await self._transport.send({"type": "session.input_audio.append", "audio": b64})

    async def close(self) -> None:
        await self._transport.close()

    async def _handle(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "session.started":
            await self._sink.emit({"type": "status", "state": "ready"})
        elif kind == "session.output_audio.delta":
            await self._sink.emit({"type": "audio", "data": ev["delta"]})
        elif kind == "session.input_transcript.delta":
            await self._sink.emit({"type": "transcript", "role": "user", "delta": ev["delta"]})
        elif kind == "session.output_transcript.delta":
            await self._sink.emit({"type": "transcript", "role": "assistant", "delta": ev["delta"]})
        elif kind == "response.event":
            await self._on_backend_event(ev["event"])
        elif kind == "error":
            self._audit.record("upstream_error", error=ev)
            await self._sink.emit({"type": "error", "message": json.dumps(ev.get("error", ev))[:300]})

    async def _on_backend_event(self, inner: dict[str, Any]) -> None:
        item = inner.get("item") or {}
        if inner.get("type") == "response.output_item.done" and item.get("type") == "function_call":
            task = asyncio.create_task(self._run_call(item))
            self._calls.add(task)
            task.add_done_callback(self._calls.discard)

    async def _run_call(self, call: dict[str, Any]) -> None:
        try:
            args = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        await self._sink.emit({"type": "tool_call", "tool": call["name"], "args": args})
        try:
            result = await self._executor.execute(call["name"], args)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        await self._sink.emit({"type": "tool_result", "tool": call["name"], "ok": result.get("ok", False),
                               "error": str(result.get("error", ""))[:200],
                               "summary": str(result.get("summary", ""))[:120]})
        await self._transport.send({
            "type": "response.item.create",
            "item": {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)},
        })
        await self._transport.send({"type": "response.create"})
