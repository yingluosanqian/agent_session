"""Claude Code CLI backend.

Drives `claude -p --output-format json` non-interactively. Implements
the `AgentSession` protocol from `agent_session._common`. Tested
against Claude Code CLI 2.1+.

- Session id is pre-assigned via `--session-id <uuid>` on turn 1.
  Turn 2+ uses `--resume <uuid>`.
- `SANDBOX_READ_ONLY` is approximated by restricting `--allowedTools`
  to read-only entries (Read / Glob / Grep / WebFetch / WebSearch).
  Other sandbox modes pass `--dangerously-skip-permissions` and rely
  on the subprocess `cwd` to confine writes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Sequence

from agent_session._common import (
    BACKEND_CLAUDE,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    AgentError,
    AgentResult,
)

log = logging.getLogger(__name__)

# Read-only sandbox = no write tools at all.
READ_ONLY_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")


def _snapshot_tree(root: Path) -> dict[Path, tuple[float, int]]:
    if not root.exists():
        return {}
    snap: dict[Path, tuple[float, int]] = {}
    for p in root.rglob("*"):
        if p.is_file():
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            snap[p.relative_to(root)] = (st.st_mtime, st.st_size)
    return snap


def _diff_snapshots(
    before: dict[Path, tuple[float, int]],
    after: dict[Path, tuple[float, int]],
) -> tuple[list[Path], list[Path]]:
    created: list[Path] = []
    modified: list[Path] = []
    for path, meta in after.items():
        prev = before.get(path)
        if prev is None:
            created.append(path)
        elif prev != meta:
            modified.append(path)
    return sorted(created), sorted(modified)


class ClaudeSession:
    """Multi-turn Claude Code session with pre-assigned session-id."""

    backend: str = BACKEND_CLAUDE

    def __init__(
        self,
        cwd: Path | str,
        binary: str = "claude",
        model: str | None = None,
        sandbox: str = SANDBOX_WORKSPACE_WRITE,
        timeout_sec: float = 3600.0,
        extra_args: Sequence[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        resolved = shutil.which(binary)
        if resolved is None:
            raise AgentError(f"claude binary not found on PATH: {binary}")
        self.binary = resolved
        self.cwd = Path(cwd).resolve()
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.sandbox = sandbox
        self.timeout_sec = timeout_sec
        self.extra_args = list(extra_args or [])
        self.extra_env: dict[str, str] = dict(extra_env or {})
        self.session_id: str | None = None
        self.turn_count: int = 0

    def _sandbox_argv(self) -> list[str]:
        if self.sandbox == SANDBOX_READ_ONLY:
            return [
                "--allowedTools", ",".join(READ_ONLY_TOOLS),
                "--dangerously-skip-permissions",
            ]
        return ["--dangerously-skip-permissions"]

    def _build_argv(self, prompt: str) -> list[str]:
        argv: list[str] = [self.binary]
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())
            argv += ["--session-id", self.session_id]
        else:
            argv += ["--resume", self.session_id]
        argv += ["-p", prompt, "--output-format", "json"]
        argv += self._sandbox_argv()
        if self.model:
            argv += ["--model", self.model]
        argv += self.extra_args
        return argv

    def send(
        self,
        prompt: str,
        timeout_sec: float | None = None,
    ) -> AgentResult:
        snap_before = _snapshot_tree(self.cwd)
        argv = self._build_argv(prompt)
        timeout = timeout_sec if timeout_sec is not None else self.timeout_sec

        sub_env = os.environ.copy()
        sub_env["IS_SANDBOX"] = "1"
        sub_env.update(self.extra_env)

        log.info(
            "ClaudeSession.send cwd=%s sandbox=%s turn=%d session=%s",
            self.cwd, self.sandbox, self.turn_count + 1,
            self.session_id or "<new>",
        )
        log.debug("ClaudeSession.send cmd=%s", argv)

        t0 = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.cwd),
                env=sub_env,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - t0
            log.warning("ClaudeSession.send timeout after %.1fs", duration)
            return AgentResult(
                ok=False,
                exit_code=-1,
                stdout=e.stdout if isinstance(e.stdout, str) else "",
                stderr=(e.stderr if isinstance(e.stderr, str) else "")
                + f"\n[agent_session] claude timed out after {duration:.1f}s",
                final_message="",
                duration_sec=duration,
                cmd=argv,
                backend=BACKEND_CLAUDE,
                session_id=self.session_id,
            )
        duration = time.monotonic() - t0

        final_message, raw_json = _extract_result_from_json(completed.stdout)
        snap_after = _snapshot_tree(self.cwd)
        created, modified = _diff_snapshots(snap_before, snap_after)

        self.turn_count += 1
        is_error = bool(raw_json.get("is_error"))
        ok = completed.returncode == 0 and not is_error

        echoed_id = raw_json.get("session_id")
        if echoed_id and self.session_id and echoed_id != self.session_id:
            log.warning(
                "ClaudeSession: echoed session_id %s != pre-assigned %s",
                echoed_id, self.session_id,
            )

        if not ok:
            log.warning(
                "ClaudeSession.send rc=%d is_error=%s stop_reason=%s",
                completed.returncode, is_error, raw_json.get("stop_reason"),
            )

        return AgentResult(
            ok=ok,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            final_message=final_message,
            duration_sec=duration,
            files_created=created,
            files_modified=modified,
            cmd=argv,
            backend=BACKEND_CLAUDE,
            session_id=self.session_id,
            raw=raw_json,
        )


def _extract_result_from_json(stdout: str) -> tuple[str, dict]:
    if not stdout:
        return "", {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            log.warning("ClaudeSession: stdout was not valid JSON")  # parse fallback
            return "", {}
    if isinstance(data, dict):
        result = data.get("result", "") or ""
        return str(result), data
    if isinstance(data, list):
        for msg in reversed(data):
            if isinstance(msg, dict) and msg.get("type") == "result":
                return str(msg.get("result", "") or ""), msg
        return "", {}
    return "", {}
