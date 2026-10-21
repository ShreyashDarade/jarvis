"""Sandbox backed by a throwaway Docker container. Fails closed: no Docker, no code execution.

Isolation per run: no network, read-only root filesystem, all capabilities dropped, non-root user,
memory/CPU/process limits, hard timeout. The code is mounted read-only; the only host data visible
is the workspace, read-only, at /data.
"""
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from ..domain.ports import SandboxResult, SandboxUnavailable

LABEL = "jarvis=sandbox"  # the Go kill switch kills every container carrying this label
MAX_OUTPUT = 8000


WINDOWS_DOCKER = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"


def find_docker() -> str:
    """docker on PATH, else Docker Desktop's standard install location (PATH may predate the install)."""
    return shutil.which("docker") or (WINDOWS_DOCKER if os.path.exists(WINDOWS_DOCKER) else "docker")


def _clean_env() -> dict[str, str]:
    """A stray DOCKER_HOST (for example tcp://localhost:2375) would override the context we choose."""
    return {k: v for k, v in os.environ.items() if k != "DOCKER_HOST"}


class DockerSandbox:
    def __init__(self, image: str = "python:3.12-slim", workspace: Path | None = None,
                 docker: str | None = None, timeout: float = 20.0, memory: str = "256m",
                 cpus: str = "1", pids: int = 64,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self._image, self._workspace = image, workspace
        self._docker = docker or find_docker()
        self._timeout, self._memory, self._cpus, self._pids = timeout, memory, cpus, pids
        self._run = runner
        self._available: bool | None = None
        self._prefix: list[str] = []   # e.g. ["--context", "desktop-linux"]
        self._env: dict[str, str] | None = None

    def _try(self, prefix: list[str]) -> bool:
        env = _clean_env() if prefix else None
        try:
            r = self._run([self._docker, *prefix, "info"], capture_output=True, timeout=8,
                          **({"env": env} if env is not None else {}))
        except (OSError, subprocess.TimeoutExpired):
            return False
        if r.returncode == 0:
            self._prefix, self._env = prefix, env
            return True
        return False

    def available(self) -> bool:
        """Finds a working Docker connection: JARVIS_DOCKER_CONTEXT, then Docker Desktop's own, then the default."""
        if self._available is None:
            forced = os.environ.get("JARVIS_DOCKER_CONTEXT")
            candidates = [["--context", forced]] if forced else [["--context", "desktop-linux"], []]
            self._available = any(self._try(c) for c in candidates)
        return self._available

    def _docker_run(self, argv: list[str], **kw):
        extra = {"env": self._env} if self._env is not None else {}
        return self._run(argv, **kw, **extra)

    def command(self, name: str, code_dir: str) -> list[str]:
        argv = [
            self._docker, *self._prefix, "run", "--rm", "-i", "--name", name, "--label", LABEL,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--memory", self._memory, "--memory-swap", self._memory,
            "--cpus", self._cpus, "--pids-limit", str(self._pids),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--mount", f"type=bind,source={code_dir},target=/sandbox,readonly",
        ]
        if self._workspace is not None:
            argv += ["--mount", f"type=bind,source={self._workspace},target=/data,readonly"]
        return argv + ["-w", "/tmp", self._image, "python", "-I", "/sandbox/main.py"]

    def run(self, code: str, stdin: str = "", timeout: float | None = None) -> SandboxResult:
        if not self.available():
            raise SandboxUnavailable("Docker is not available, so code execution is disabled")
        limit = timeout or self._timeout
        name = f"jarvis-sbx-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix="jarvis-sbx-") as code_dir:
            (Path(code_dir) / "main.py").write_text(code, encoding="utf-8")
            try:
                r = self._docker_run(self.command(name, code_dir), input=stdin, capture_output=True,
                                     text=True, timeout=limit + 5)  # container start-up is not counted
            except subprocess.TimeoutExpired:
                self._docker_run([self._docker, *self._prefix, "kill", name], capture_output=True, timeout=10)
                return SandboxResult(-1, "", f"timed out after {limit:.0f}s", timed_out=True)
        return SandboxResult(r.returncode, r.stdout[:MAX_OUTPUT], r.stderr[:MAX_OUTPUT])
