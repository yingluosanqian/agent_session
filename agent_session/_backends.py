"""Backend selection + session factory.

`pick_backend` and `make_session` are the only entry points consumers
should call. Adding a new backend amounts to: (1) implement the
session class and import it lazily inside `make_session`; (2) add the
backend-name constant to `_common.ALL_BACKENDS`.

Selection policy in `make_session`:

  1. `backend=` kwarg, if given.
  2. `pick_backend(env_var=...)` — read env var if the consumer named
     one (e.g. `AUTOR_BACKEND` for autor). If unset, random pick across
     registered backends.

Random selection is deliberate: under parallel consumers, different
slots may end up on different backends, maximising coverage across
agent harnesses without manual orchestration.
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Sequence

from agent_session._common import (
    ALL_BACKENDS,
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    SANDBOX_WORKSPACE_WRITE,
    AgentError,
    AgentSession,
)


def pick_backend(
    *,
    env_var: str | None = None,
    rng: random.Random | None = None,
) -> str:
    """Return the name of a registered backend.

    `env_var`: if given and set in `os.environ`, the value (case-
    insensitive) overrides the random pick. Unknown values raise
    `AgentError` so typos surface loudly. If `env_var` is None or the
    var is unset, a random backend is selected via `rng or random`.
    """
    if env_var:
        forced = os.environ.get(env_var, "").strip().lower()
        if forced:
            if forced not in ALL_BACKENDS:
                raise AgentError(
                    f"{env_var}={forced!r} not in {ALL_BACKENDS}"
                )
            return forced
    r = rng if rng is not None else random
    return r.choice(ALL_BACKENDS)


def make_session(
    cwd: Path | str,
    *,
    sandbox: str = SANDBOX_WORKSPACE_WRITE,
    model: str | None = None,
    timeout_sec: float = 3600.0,
    extra_args: Sequence[str] | None = None,
    extra_env: dict[str, str] | None = None,
    backend: str | None = None,
    env_var: str | None = None,
    rng: random.Random | None = None,
) -> AgentSession:
    """Construct an `AgentSession`.

    `backend` (explicit string) takes precedence over `env_var` / random
    selection. `model`, `extra_args`, `extra_env` are forwarded to the
    backend; if a value is incompatible (e.g. a codex model name handed
    to claude), the failure surfaces on the first `send()` call, not here.
    """
    chosen = backend or pick_backend(env_var=env_var, rng=rng)
    if chosen == BACKEND_CLAUDE:
        from agent_session.claude import ClaudeSession  # noqa: PLC0415
        return ClaudeSession(
            cwd=cwd,
            sandbox=sandbox,
            model=model,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
            extra_env=extra_env,
        )
    if chosen == BACKEND_CODEX:
        from agent_session.codex import CodexSession  # noqa: PLC0415
        return CodexSession(
            cwd=cwd,
            sandbox=sandbox,
            model=model,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
            extra_env=extra_env,
        )
    raise AgentError(f"unknown backend {chosen!r}")
