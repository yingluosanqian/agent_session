"""Codex CLI backend.

Thin wrapper around the `codex exec` / `codex exec resume` CLI (tested
against codex 0.120+). Implements the `AgentSession` protocol from
`agent_session._common`.

Quirks:
- Session id is parsed from the codex banner on stderr after turn 1
  (codex itself assigns it). Subsequent turns use `codex exec resume <id>`.
- `codex exec resume` does not accept `--cd` / `-s`; cwd is set via the
  subprocess and sandbox via `-c sandbox_mode=...` on every turn.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Sequence

from agent_session._common import (
    BACKEND_CODEX,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    AgentError,
    AgentResult,
)

log = logging.getLogger(__name__)

# Aliases for legacy code that still imports concrete names.
CodexError = AgentError
CodexResult = AgentResult

__all__ = [
    "SANDBOX_READ_ONLY",
    "SANDBOX_WORKSPACE_WRITE",
    "SANDBOX_DANGER_FULL_ACCESS",
    "CodexError",
    "CodexResult",
    "CodexClient",
    "CodexSession",
]


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


class CodexClient:
    """Run `codex exec` one-shot prompts and report what changed on disk.

    Each call to `run()` spawns a fresh codex subprocess. Codex may create
    or modify files under the working directory subject to the chosen
    sandbox policy; the returned `CodexResult` lists exactly which files
    were touched so callers can validate outputs without parsing stdout.
    """

    def __init__(
        self,
        binary: str = "codex",
        model: str | None = None,
        sandbox: str = SANDBOX_WORKSPACE_WRITE,
        skip_git_check: bool = True,
        ephemeral: bool = False,
        timeout_sec: float = 3600.0,
        extra_args: Sequence[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        resolved = shutil.which(binary)
        if resolved is None:
            raise CodexError(f"codex binary not found on PATH: {binary}")
        self.binary = resolved
        self.model = model
        self.sandbox = sandbox
        self.skip_git_check = skip_git_check
        self.ephemeral = ephemeral
        self.timeout_sec = timeout_sec
        self.extra_args = list(extra_args or [])
        self.extra_env: dict[str, str] = dict(extra_env or {})

    def version(self) -> str:
        out = subprocess.run(
            [self.binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (out.stdout or out.stderr).strip()

    def _build_argv(
        self,
        prompt: str,
        cwd: Path | None,
        last_message_path: Path,
    ) -> list[str]:
        argv: list[str] = [self.binary, "exec"]
        if self.model:
            argv += ["--model", self.model]
        if self.sandbox:
            argv += ["--sandbox", self.sandbox]
        if cwd is not None:
            argv += ["--cd", str(cwd)]
        if self.skip_git_check:
            argv += ["--skip-git-repo-check"]
        if self.ephemeral:
            argv += ["--ephemeral"]
        argv += ["--output-last-message", str(last_message_path)]
        argv += self.extra_args
        argv.append(prompt)
        return argv

    def run(
        self,
        prompt: str,
        cwd: Path | str | None = None,
        timeout_sec: float | None = None,
    ) -> CodexResult:
        cwd_path = Path(cwd).resolve() if cwd is not None else None
        if cwd_path is not None:
            cwd_path.mkdir(parents=True, exist_ok=True)

        tmp = tempfile.NamedTemporaryFile(
            prefix="codex_last_", suffix=".txt", delete=False
        )
        tmp.close()
        last_message_path = Path(tmp.name)

        try:
            snap_before = _snapshot_tree(cwd_path) if cwd_path else {}
            argv = self._build_argv(
                prompt=prompt,
                cwd=cwd_path,
                last_message_path=last_message_path,
            )
            timeout = timeout_sec if timeout_sec is not None else self.timeout_sec

            log.info("codex.run cwd=%s timeout=%s", cwd_path, timeout)
            log.debug("codex.run cmd=%s", argv)

            t0 = time.monotonic()
            sub_env = os.environ.copy()
            sub_env.update(self.extra_env)
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=sub_env,
                )
            except subprocess.TimeoutExpired as e:
                duration = time.monotonic() - t0
                log.warning("codex.run timeout after %.1fs", duration)
                return CodexResult(
                    ok=False,
                    exit_code=-1,
                    stdout=e.stdout or "",
                    stderr=(e.stderr or "") + f"\n[agent_session] timed out after {duration:.1f}s",
                    final_message="",
                    duration_sec=duration,
                    cmd=argv,
                    backend=BACKEND_CODEX,
                )
            duration = time.monotonic() - t0

            final_message = ""
            try:
                final_message = last_message_path.read_text()
            except (FileNotFoundError, OSError):
                pass

            snap_after = _snapshot_tree(cwd_path) if cwd_path else {}
            created, modified = _diff_snapshots(snap_before, snap_after)

            ok = completed.returncode == 0
            if not ok:
                log.warning(
                    "codex.run exit=%d stderr=%s",
                    completed.returncode,
                    (completed.stderr or "")[:500],
                )

            return CodexResult(
                ok=ok,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                final_message=final_message,
                duration_sec=duration,
                files_created=created,
                files_modified=modified,
                cmd=argv,
                backend=BACKEND_CODEX,
            )
        finally:
            try:
                last_message_path.unlink()
            except FileNotFoundError:
                pass


_SESSION_ID_RE = re.compile(
    r"session id:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _parse_session_id(stdout: str) -> str | None:
    match = _SESSION_ID_RE.search(stdout or "")
    return match.group(1) if match else None


class CodexSession:
    """Multi-turn codex session backed by `codex exec` + `codex exec resume`.

    The first `send()` runs `codex exec`, parses the session UUID from the
    banner on stdout, then uses `codex exec resume <id>` for subsequent
    turns. The on-disk session transcript maintained by codex itself acts
    as long-term memory: each resume call replays the full history into
    the model context.

    `codex exec resume` does not accept `--cd` or `-s`, so the working
    directory is set via the subprocess cwd, and the sandbox mode is
    passed via `-c sandbox_mode=<value>` on every turn (both start and
    resume) for a uniform argv shape.
    """

    backend: str = BACKEND_CODEX

    def __init__(
        self,
        cwd: Path | str,
        binary: str = "codex",
        model: str | None = None,
        sandbox: str = SANDBOX_WORKSPACE_WRITE,
        skip_git_check: bool = True,
        timeout_sec: float = 3600.0,
        extra_args: Sequence[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        resolved = shutil.which(binary)
        if resolved is None:
            raise CodexError(f"codex binary not found on PATH: {binary}")
        self.binary = resolved
        self.cwd = Path(cwd).resolve()
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.sandbox = sandbox
        self.skip_git_check = skip_git_check
        self.timeout_sec = timeout_sec
        self.extra_args = list(extra_args or [])
        self.extra_env: dict[str, str] = dict(extra_env or {})
        self.session_id: str | None = None
        self.turn_count: int = 0

    def _config_args(self) -> list[str]:
        argv: list[str] = []
        if self.sandbox:
            argv += ["-c", f'sandbox_mode="{self.sandbox}"']
        if self.model:
            argv += ["-m", self.model]
        if self.skip_git_check:
            argv += ["--skip-git-repo-check"]
        argv += self.extra_args
        return argv

    def _build_argv(self, prompt: str, last_msg_path: Path) -> list[str]:
        argv: list[str] = [self.binary, "exec"]
        if self.session_id is not None:
            argv.append("resume")
        argv += self._config_args()
        argv += ["--output-last-message", str(last_msg_path)]
        if self.session_id is not None:
            argv.append(self.session_id)
        argv.append(prompt)
        return argv

    def send(
        self,
        prompt: str,
        timeout_sec: float | None = None,
    ) -> CodexResult:
        tmp = tempfile.NamedTemporaryFile(
            prefix="codex_last_", suffix=".txt", delete=False
        )
        tmp.close()
        last_msg_path = Path(tmp.name)

        try:
            snap_before = _snapshot_tree(self.cwd)
            argv = self._build_argv(prompt, last_msg_path)
            timeout = timeout_sec if timeout_sec is not None else self.timeout_sec

            log.info(
                "CodexSession.send cwd=%s sandbox=%s turn=%d session=%s",
                self.cwd,
                self.sandbox,
                self.turn_count + 1,
                self.session_id or "<new>",
            )
            log.debug("CodexSession.send cmd=%s", argv)

            t0 = time.monotonic()
            sub_env = os.environ.copy()
            sub_env.update(self.extra_env)
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
                log.warning("CodexSession.send timeout after %.1fs", duration)
                return CodexResult(
                    ok=False,
                    exit_code=-1,
                    stdout=e.stdout or "",
                    stderr=(e.stderr or "") + f"\n[agent_session] timed out after {duration:.1f}s",
                    final_message="",
                    duration_sec=duration,
                    cmd=argv,
                    backend=BACKEND_CODEX,
                    session_id=self.session_id,
                )
            duration = time.monotonic() - t0

            final_message = ""
            try:
                final_message = last_msg_path.read_text()
            except (FileNotFoundError, OSError):
                pass

            snap_after = _snapshot_tree(self.cwd)
            created, modified = _diff_snapshots(snap_before, snap_after)

            if self.session_id is None:
                # codex writes the session-id banner to stderr; stdout is
                # reserved for the agent's own output. Search both defensively.
                parsed = (
                    _parse_session_id(completed.stderr)
                    or _parse_session_id(completed.stdout)
                )
                if parsed is None:
                    raise CodexError(
                        "could not parse session id from codex output "
                        f"(exit={completed.returncode}). First 400 chars of "
                        f"stderr:\n{(completed.stderr or '')[:400]}"
                    )
                self.session_id = parsed

            self.turn_count += 1

            ok = completed.returncode == 0
            if not ok:
                log.warning(
                    "CodexSession.send exit=%d stderr=%s",
                    completed.returncode,
                    (completed.stderr or "")[:500],
                )

            return CodexResult(
                ok=ok,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                final_message=final_message,
                duration_sec=duration,
                files_created=created,
                files_modified=modified,
                cmd=argv,
                backend=BACKEND_CODEX,
                session_id=self.session_id,
            )
        finally:
            try:
                last_msg_path.unlink()
            except FileNotFoundError:
                pass
