"""agent_session — uniform multi-turn session over CLI coding agents.

Public API:

    from agent_session import (
        # session factory + backend selection
        make_session, pick_backend,
        # types
        AgentSession, AgentResult, AgentError,
        # sandbox modes
        SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE, SANDBOX_DANGER_FULL_ACCESS,
        # backend names
        BACKEND_CLAUDE, BACKEND_CODEX, ALL_BACKENDS,
    )

Quick start:

    from agent_session import make_session, SANDBOX_WORKSPACE_WRITE

    sess = make_session(
        cwd="/tmp/scratch",
        sandbox=SANDBOX_WORKSPACE_WRITE,
        backend="claude",         # explicit; or use env_var="MY_PROJ_BACKEND"
        timeout_sec=600,
    )
    r = sess.send("Write hello.txt with the word 'hi'")
    print(r.ok, r.files_created, r.final_message)
    r = sess.send("Now add the word 'there'.")  # turn 2: same session id

See README.md for backend-specific quirks and the env-var pattern most
consumers use.
"""

from agent_session._common import (
    ALL_BACKENDS,
    ALL_SANDBOXES,
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    AgentError,
    AgentResult,
    AgentSession,
)
from agent_session._backends import make_session, pick_backend

__version__ = "0.1.0"

__all__ = [
    "AgentError",
    "AgentResult",
    "AgentSession",
    "ALL_BACKENDS",
    "ALL_SANDBOXES",
    "BACKEND_CLAUDE",
    "BACKEND_CODEX",
    "SANDBOX_DANGER_FULL_ACCESS",
    "SANDBOX_READ_ONLY",
    "SANDBOX_WORKSPACE_WRITE",
    "make_session",
    "pick_backend",
]
