import json
import threading
import time
from pathlib import Path
from typing import Any


class FileAuditLog:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()

    def record(self, kind: str, **data: Any) -> None:
        line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "src": "ai", "kind": kind, **data})
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
