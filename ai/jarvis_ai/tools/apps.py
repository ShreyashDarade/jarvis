"""Find and open apps installed on this PC (Win32 and Store apps), via the Start menu's own app list.

The model only ever supplies a *name*. The thing that gets launched is an app id taken from the
index, never a path or command from the model.
"""
import difflib
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..domain.ports import Risk

CACHE_SECONDS = 300
AMBIGUITY_MARGIN = 0.08
LAUNCH_THRESHOLD = 0.7   # weak matches are suggestions for find_apps, never launched


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str


def _windows_start_apps() -> list[AppEntry]:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    data = json.loads(r.stdout)
    data = [data] if isinstance(data, dict) else data
    return [AppEntry(d["Name"], d["AppID"]) for d in data if d.get("Name") and d.get("AppID")]


def _launch(app_id: str) -> None:
    # explorer opens shell:AppsFolder\<id> exactly like clicking the Start menu entry
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])


FILLER_WORDS = frozenset({"application", "app", "program", "software", "tool", "desktop", "client"})

# Generic speech-mishearing tolerance: applies the same way to every app name, never seeded with a
# particular app. Vowels are the part of a word speech recognition confuses most, so comparing
# consonant "skeletons" catches most mishearings (cloud code/claude, curser/cursor, anti gravity/
# antigravity) without a per-app list, and a name genuinely must share most of its consonants in
# order, so unrelated apps (cursor vs discord) score far lower; see tests/test_apps.py for the margin.
_SKELETON_MIN, _SKELETON_FLOOR, _SKELETON_CEIL = 0.55, 0.65, 0.95


def _skeleton(s: str) -> str:
    letters = "".join(ch for ch in s.lower() if ch.isalpha())
    if not letters:
        return ""
    head, rest = letters[0], letters[1:]
    rest = "".join(ch for ch in rest if ch not in "aeiouy")
    deduped = head
    for ch in rest:
        if ch != deduped[-1]:
            deduped += ch
    return deduped


def _skeleton_score(query: str, name: str) -> float:
    sq, sn = _skeleton(query), _skeleton(name)
    if not sq or not sn:
        return 0.0
    ratio = difflib.SequenceMatcher(None, sq, sn).ratio()
    if ratio < _SKELETON_MIN:
        return 0.0
    span = 1.0 - _SKELETON_MIN
    return _SKELETON_FLOOR + (_SKELETON_CEIL - _SKELETON_FLOOR) * (ratio - _SKELETON_MIN) / span


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("-", " ").replace("_", " ").split())


def _clean_query(query: str) -> str:
    words = _norm(query).split()
    if words and words[0] in {"open", "launch", "start"}:
        words = words[1:]
    while len(words) > 1 and words[-1] in FILLER_WORDS:
        words.pop()
    return " ".join(words)


def score(query: str, name: str) -> float:
    q_orig, n = _norm(query), _norm(name)
    q = _clean_query(query)
    if not q:
        return 0.0
    if q == n or q_orig == n:
        return 1.0
    if n.startswith(q) or q.startswith(n):
        return 0.92
    if q in n:
        return 0.85
    q_words, n_words = set(q.split()), set(n.split())
    if q_words and q_words <= n_words:
        return 0.8
    return max(_skeleton_score(q, n), difflib.SequenceMatcher(None, q, n).ratio() * 0.75)


class StartMenuIndex:
    def __init__(self, loader: Callable[[], list[AppEntry]] = _windows_start_apps, ttl: float = CACHE_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._loader, self._ttl, self._clock = loader, ttl, clock
        self._cache: list[AppEntry] | None = None
        self._at = 0.0

    def apps(self) -> list[AppEntry]:
        if self._cache is None or self._clock() - self._at > self._ttl:
            self._cache, self._at = self._loader(), self._clock()
        return self._cache

    def match(self, query: str, limit: int = 5, minimum: float = 0.5) -> list[tuple[AppEntry, float]]:
        ranked = sorted(((a, score(query, a.name)) for a in self.apps()), key=lambda p: -p[1])
        return [(a, s) for a, s in ranked[:limit] if s >= minimum]


class FindApps:
    name = "find_apps"
    description = ("Search the apps installed on this PC by name. Use it when the user names an app you are "
                   "unsure about, or to list what is available. Speech recognition may mishear any app's name, "
                   "so try close spellings before giving up.")
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    risk = Risk.READ

    def __init__(self, index: StartMenuIndex):
        self._index = index

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        matches = self._index.match(str(args.get("query", "")), limit=8)
        return {"ok": True, "apps": [a.name for a, _ in matches]}


class OpenApp:
    name = "open_app"
    description = ("Open an installed app by name, exactly like clicking it in the Start menu. Always try this "
                   "when the user asks to open or start a program. If the name is ambiguous the result lists "
                   "the candidates; pick one and call again with its exact name.")
    parameters = {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]}
    risk = Risk.CHANGE

    def __init__(self, index: StartMenuIndex, launch: Callable[[str], Any] = _launch, desktop: Any = None):
        self._index, self._launch, self._desktop = index, launch, desktop

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("app", ""))
        matches = self._index.match(query, minimum=LAUNCH_THRESHOLD)
        if not matches:
            return {"ok": False, "summary": f"No installed app matches “{query}”",
                    "error": f"no installed app matches '{query}'. Try find_apps with a different spelling."}
        (best, best_score), rest = matches[0], matches[1:]
        if best_score < 1.0 and rest and rest[0][1] >= best_score - AMBIGUITY_MARGIN:
            return {"ok": False, "error": "ambiguous; call again with one exact name",
                    "summary": "Several apps match “" + query + "”: " + ", ".join(a.name for a, _ in matches[:3]),
                    "candidates": [a.name for a, _ in matches]}
        self._launch(best.app_id)

        # Wait briefly for the window to appear and bring it to the foreground
        opened_window = None
        if self._desktop and hasattr(self._desktop, "find_by_title_or_process"):
            for _ in range(25):
                time.sleep(0.1)
                win = self._desktop.find_by_title_or_process(best.name)
                if win:
                    self._desktop.activate(win)
                    opened_window = win
                    break

        res: dict[str, Any] = {"ok": True, "opened": best.name, "summary": f"Opened {best.name}"}
        if opened_window:
            res["window"] = {"id": opened_window.window_id, "title": opened_window.title}
            res["summary"] = f"Opened {best.name} and focused its window"
        return res

