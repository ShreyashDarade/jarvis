"""Abstractions the application layer depends on (Dependency Inversion).

Concrete implementations live in `tools/`, `policy/`, `sandbox/` and `adapters/`.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


class Risk(enum.Enum):
    READ = "read"       # observes only
    CHANGE = "change"   # changes the machine or leaves it (opens apps, writes files, runs code, ...)


@dataclass(frozen=True)
class ApprovalRequest:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    """`run` may be a plain function (executed in a worker thread) or a coroutine function."""
    name: str
    description: str
    parameters: dict[str, Any]   # JSON schema
    risk: Risk
    # Tools that manipulate the live desktop may opt out of every session-level
    # shortcut.  This keeps the approval decision attached to the exact target
    # and payload the user sees.
    always_approve: bool

    def run(self, args: dict[str, Any]) -> Any: ...


class ApprovalPolicy(Protocol):
    def requires_approval(self, tool: Tool) -> bool: ...


class Approver(Protocol):
    async def approve(self, request: ApprovalRequest) -> bool: ...


class ToolInvoker(Protocol):
    """Runs a tool call with approval, execution and audit. Agents call tools only through this."""
    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


class EventSink(Protocol):
    """Where events for the user interface go."""
    async def emit(self, event: dict[str, Any]) -> None: ...


class AuditLog(Protocol):
    def record(self, kind: str, **data: Any) -> None: ...


class LiveTransport(Protocol):
    """A connection to the voice model."""
    async def open(self) -> None: ...
    async def send(self, event: dict[str, Any]) -> None: ...
    def events(self) -> AsyncIterator[dict[str, Any]]: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxUnavailable(RuntimeError):
    pass


class Sandbox(Protocol):
    """Runs untrusted Python in isolation. Must raise SandboxUnavailable rather than run unisolated."""
    def run(self, code: str, stdin: str = "", timeout: float | None = None) -> SandboxResult: ...


class ModelClient(Protocol):
    """Text/tool model used by agents (Responses API shape)."""
    async def create(self, model: str, instructions: str, input: list[dict[str, Any]],
                     tools: list[dict[str, Any]]) -> dict[str, Any]: ...
