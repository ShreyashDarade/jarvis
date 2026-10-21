import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # jarvis-app/

OFF_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    api_key: str
    port: int
    secret: str
    backend_model: str
    code_model: str
    voice: str
    workspace: Path
    audit_log: Path
    sandbox_image: str
    windows_ui_control: bool

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            api_key=env.get("OPENAI_API_KEY", ""),
            port=int(env.get("JARVIS_AI_PORT", "8766")),
            secret=env.get("JARVIS_AI_SECRET", ""),
            backend_model=env.get("JARVIS_BACKEND_MODEL", "gpt-5.5"),
            code_model=env.get("JARVIS_CODE_MODEL", "gpt-6.1-sol"),
            voice=env.get("JARVIS_VOICE", "marin"),
            workspace=Path(env.get("JARVIS_WORKSPACE", ROOT / "workspace")),
            audit_log=Path(env.get("JARVIS_AUDIT_LOG", ROOT / "jarvis_audit.log")),
            sandbox_image=env.get("JARVIS_SANDBOX_IMAGE", "python:3.12-slim"),
            # On by default at the owner's request. Every click, key press and typed text still needs its own
            # approval and can never be pre-approved. Turn it off with JARVIS_WINDOWS_UI_CONTROL=0
            # (or start.ps1 -NoWindowsUi).
            windows_ui_control=env.get("JARVIS_WINDOWS_UI_CONTROL", "1").strip().lower() not in OFF_VALUES,
        )
