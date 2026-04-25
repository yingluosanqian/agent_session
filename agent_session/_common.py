"""Shared types and constants for every backend.

Backends (`agent_session.claude`, `agent_session.codex`) implement the
`AgentSession` protocol and return `AgentResult` instances. Consumers
(`autor`, `aker`, …) only depend on these stable types — never on a
specific backend module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# --------------------------- sandbox modes ---------------------------

SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"

ALL_SANDBOXES: tuple[str, ...] = (
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    SANDBOX_DANGER_FULL_ACCESS,
)

# --------------------------- backend names ---------------------------

BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"

ALL_BACKENDS: tuple[str, ...] = (BACKEND_CLAUDE, BACKEND_CODEX)


# --------------------------- exceptions ------------------------------


class AgentError(RuntimeError):
    """Raised when an agent binary is missing or backend is misconfigured."""


# --------------------------- result type -----------------------------


@dataclass
class AgentResult:
    """Backend-agnostic outcome of one `AgentSession.send()` call.

    Callers should only read these canonical fields. Backends may attach
    extra debug data on `raw` but must not require callers to look at it.
    """

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    final_message: str
    duration_sec: float
    files_created: list[Path] = field(default_factory=list)
    files_modified: list[Path] = field(default_factory=list)
    cmd: list[str] = field(default_factory=list)
    backend: str = ""
    session_id: str | None = None
    raw: dict = field(default_factory=dict)


# --------------------------- session protocol ------------------------


class AgentSession(Protocol):
    """Multi-turn session against one CLI agent.

    Every backend's session class must implement `send(prompt, timeout_sec=None)`
    and expose `cwd`, `session_id`, `turn_count`, `backend`. Callers create
    a session via `agent_session.make_session(cwd, sandbox=..., ...)` and
    feed it prompts; turn 1 starts a fresh session and turn 2+ resumes it.

    Sessions are not thread-safe — each parallel slot in a consumer should
    create its own session.
    """

    cwd: Path
    session_id: str | None
    turn_count: int
    backend: str  # one of `ALL_BACKENDS`

    def send(
        self,
        prompt: str,
        timeout_sec: float | None = None,
    ) -> AgentResult: ...
