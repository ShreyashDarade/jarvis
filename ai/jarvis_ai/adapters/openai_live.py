"""LiveTransport for OpenAI's gpt-live-1 over WebSocket."""
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import websockets

URL = "wss://api.openai.com/v1/live/sessions"
LIVE_MODEL = "gpt-live-1"
RATE = 24000


@dataclass(frozen=True)
class LiveSettings:
    api_key: str
    backend_model: str
    voice: str
    tool_schemas: list[dict[str, Any]]
    live_instructions: str
    backend_instructions: str


def build_session_start(s: LiveSettings) -> dict[str, Any]:
    return {
        "type": "session.start",
        "event_id": "event_start",
        "session": {
            "model": LIVE_MODEL,
            "instructions": s.live_instructions,
            "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": s.voice}},
            "delegation": {"type": "responses", "responses": {
                "model": s.backend_model,
                "instructions": s.backend_instructions,
                "tools": s.tool_schemas,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            }},
        },
    }


class OpenAILiveTransport:
    def __init__(self, settings: LiveSettings):
        self._settings = settings
        self._ws = None

    async def open(self) -> None:
        self._ws = await websockets.connect(
            URL, additional_headers={"Authorization": f"Bearer {self._settings.api_key}"}
        )
        await self.send(build_session_start(self._settings))

    async def send(self, event: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(event))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        async for message in self._ws:
            event = json.loads(message)
            yield event
            if event.get("type") == "session.closed":
                return

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "session.close"}))
        except Exception:
            pass
        await self._ws.close()
