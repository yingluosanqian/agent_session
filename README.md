# agent_session

A small, single-purpose Python library: **uniform multi-turn session
abstraction over CLI coding agents** (Claude Code, Codex). One protocol,
two interchangeable backends, zero opinions about what you do with the
result.

It exists because higher-level frameworks (`autor`, `aker`, ...) all
need the same primitive — "drive a `claude -p` or `codex exec`
subprocess across multiple turns, capture file changes, return a
canonical result" — and re-implementing it per project drifts.

```python
from agent_session import (
    make_session,
    SANDBOX_WORKSPACE_WRITE, SANDBOX_READ_ONLY,
    BACKEND_CLAUDE, BACKEND_CODEX,
)

sess = make_session(
    cwd="/tmp/scratch",
    sandbox=SANDBOX_WORKSPACE_WRITE,
    backend=BACKEND_CLAUDE,         # explicit; or use env_var=...
    timeout_sec=600,
)

r = sess.send("Write hello.txt with 'hi' inside.")
assert r.ok
print(r.files_created)              # [PosixPath('hello.txt')]
print(r.final_message)
print(r.session_id)                 # uuid string

r2 = sess.send("Now append ' there'.")  # turn 2: same session_id, resumed
```

---

## Public API

Everything below is importable from the top-level `agent_session` namespace.
Anything inside `agent_session._*` modules is internal — do not import
from there.

### Session factory

| symbol | what it does |
|---|---|
| `make_session(cwd, *, sandbox, model, timeout_sec, extra_args, extra_env, backend, env_var, rng)` | Construct an `AgentSession`. Picks a backend per `backend=` / `env_var=` / random. |
| `pick_backend(*, env_var, rng)` | Choose backend name without instantiating. |

### Types

| symbol | shape |
|---|---|
| `AgentSession` (protocol) | `cwd: Path`, `session_id: str \| None`, `turn_count: int`, `backend: str`, `send(prompt, timeout_sec=None) -> AgentResult` |
| `AgentResult` (dataclass) | `ok, exit_code, stdout, stderr, final_message, duration_sec, files_created, files_modified, cmd, backend, session_id, raw` |
| `AgentError` (Exception) | Raised for missing binaries / unknown backend / parse failures. |

### Constants

| group | values |
|---|---|
| sandbox modes | `SANDBOX_READ_ONLY` `SANDBOX_WORKSPACE_WRITE` `SANDBOX_DANGER_FULL_ACCESS` |
| backend names | `BACKEND_CLAUDE` `BACKEND_CODEX` `ALL_BACKENDS` |

---

## Sandbox semantics

| sandbox | claude implementation | codex implementation |
|---|---|---|
| `read-only` | `--allowedTools Read,Glob,Grep,WebFetch,WebSearch --dangerously-skip-permissions` | `--sandbox read-only` |
| `workspace-write` | `--dangerously-skip-permissions` (writes confined by subprocess `cwd`) | `--sandbox workspace-write` |
| `danger-full-access` | same as workspace-write at the CLI level — no extra restriction | `--sandbox danger-full-access` |

Sandbox is enforced **per-turn**, not session-wide; resuming a session
with a different sandbox is allowed (within each backend's own constraints).

`workspace-write` and `danger-full-access` look identical for claude
because Claude Code does not currently expose a finer-grained native
sandbox; consumers that need stricter confinement should rely on the
subprocess `cwd` + filesystem permissions.

---

## Backend selection

Three places control which backend `make_session` picks, in priority order:

1. `backend="claude"` / `backend="codex"` — explicit kwarg, no override.
2. `env_var="MY_PROJ_BACKEND"` — read that env var (case-insensitive,
   value must be in `ALL_BACKENDS`); raises `AgentError` on unknown
   values so typos are loud.
3. Random — uniform pick across `ALL_BACKENDS` via the supplied or
   default `random.Random`.

Consumers typically expose their own env var name:

```python
sess = make_session(cwd=..., env_var="AUTOR_BACKEND")  # autor convention
sess = make_session(cwd=..., env_var="AKER_BACKEND")   # aker convention
```

---

## Result reporting

Every backend computes `files_created` / `files_modified` by snapshotting
mtimes + sizes of `cwd.rglob('*')` before and after the subprocess.
Snapshot ignores nothing — if your subprocess writes Python bytecode
into `__pycache__/`, that will show up. Filtering is the consumer's
responsibility.

`final_message` is the agent's last assistant turn:
- claude: `result` field of `claude -p --output-format json` output
- codex: contents of the file pointed to by `--output-last-message`

If the subprocess times out, `ok=False`, `exit_code=-1`, and `stderr`
contains the timeout note.

---

## Prerequisites

- Python ≥ 3.9
- `claude` binary (Claude Code CLI 2.0+) on `PATH` if you want the claude backend
- `codex` binary (codex 0.120+) on `PATH` if you want the codex backend

You only need the binaries for backends you actually instantiate. The
package imports lazily — `make_session(..., backend="claude")` does not
import `agent_session.codex` and vice versa.

---

## Install

```bash
git clone <this repo>
cd agent_session
pip install -e . --user
```

---

## What this is NOT

- **Not a graph framework** — that lives in consumers (`autor`, `aker`).
- **Not a prompt library** — `send` takes a string, no templating.
- **Not multi-process** — sessions are not thread-safe; spawn one per
  worker if you parallelise upstream.
- **Not deterministic** — both claude and codex have non-zero
  temperature; this library does not change that.

---

## Adding a new backend

1. Implement `MyAgentSession` somewhere in `agent_session/<name>.py`
   conforming to `AgentSession` protocol.
2. Add `BACKEND_MYAGENT = "myagent"` to `agent_session/_common.py`
   and append it to `ALL_BACKENDS`.
3. In `agent_session/_backends.py:make_session`, add a branch that
   imports your module lazily and constructs the session.
4. Optionally add a smoke test to `tests/`.
