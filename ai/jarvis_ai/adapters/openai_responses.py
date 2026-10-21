"""ModelClient over the OpenAI Responses API (used by agents; function calling needs this API)."""
from typing import Any

import httpx

URL = "https://api.openai.com/v1/responses"


class OpenAIResponsesClient:
    def __init__(self, api_key: str, timeout: float = 180.0, client: httpx.AsyncClient | None = None):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = client or httpx.AsyncClient(timeout=timeout)

    async def create(self, model: str, instructions: str, input: list[dict[str, Any]],
                     tools: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "instructions": instructions, "input": input, "store": False}
        if tools:
            body["tools"] = tools
        r = await self._http.post(URL, headers=self._headers, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"model error {r.status_code}: {r.text[:300]}")
        return r.json()
