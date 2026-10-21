import webbrowser
from typing import Any, Callable

from ..domain.ports import Risk


class OpenUrl:
    name = "open_url"
    description = "Open an http(s) URL in the default browser."
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    risk = Risk.CHANGE

    def __init__(self, opener: Callable[[str], Any] = webbrowser.open):
        self._open = opener

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "only http(s) URLs"}
        self._open(url)
        return {"ok": True}
