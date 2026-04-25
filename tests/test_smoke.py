"""Smoke tests for agent_session.

Most checks here are import + signature integrity — they don't actually
spawn `claude` / `codex` (those are integration concerns). The one exception
is the round-trip backend-pick test, which uses a deterministic RNG.
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_session as ag


_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        _failures.append(f"{label}: {detail}".rstrip(": "))
        print(f"  FAIL  {label}  {detail}")
    else:
        print(f"  ok    {label}")


def eq(label: str, actual, expected) -> None:
    check(label, actual == expected, f"got {actual!r} expected {expected!r}")


def test_public_api_exports():
    print("\n[public API exports]")
    expected = {
        "make_session", "pick_backend",
        "AgentSession", "AgentResult", "AgentError",
        "SANDBOX_READ_ONLY", "SANDBOX_WORKSPACE_WRITE", "SANDBOX_DANGER_FULL_ACCESS",
        "BACKEND_CLAUDE", "BACKEND_CODEX", "ALL_BACKENDS",
        "ALL_SANDBOXES",
    }
    for name in expected:
        check(f"`agent_session.{name}` exists", hasattr(ag, name))


def test_pick_backend_random_in_range():
    print("\n[pick_backend rng pick]")
    rng = random.Random(42)
    for _ in range(50):
        b = ag.pick_backend(rng=rng)
        check(f"pick {b!r} in ALL_BACKENDS", b in ag.ALL_BACKENDS)
        if b not in ag.ALL_BACKENDS:
            break


def test_pick_backend_env_override():
    print("\n[pick_backend env override]")
    os.environ["TEST_AGENT_BACKEND_X"] = "claude"
    try:
        eq("env override claude", ag.pick_backend(env_var="TEST_AGENT_BACKEND_X"), "claude")
        os.environ["TEST_AGENT_BACKEND_X"] = "CODEX"
        eq("case insensitive", ag.pick_backend(env_var="TEST_AGENT_BACKEND_X"), "codex")
        os.environ["TEST_AGENT_BACKEND_X"] = "garbage"
        try:
            ag.pick_backend(env_var="TEST_AGENT_BACKEND_X")
            check("unknown raises", False, "expected AgentError")
        except ag.AgentError:
            check("unknown raises", True)
    finally:
        del os.environ["TEST_AGENT_BACKEND_X"]


def test_pick_backend_no_env_unset_falls_through():
    print("\n[pick_backend env_var unset → rng pick]")
    rng = random.Random(0)
    # Var name that almost certainly doesn't exist
    b = ag.pick_backend(env_var="_DEFINITELY_NOT_SET_X9Q", rng=rng)
    check(f"falls through to rng pick → {b!r}", b in ag.ALL_BACKENDS)


def test_make_session_unknown_backend_raises():
    print("\n[make_session unknown backend]")
    with tempfile.TemporaryDirectory() as t:
        try:
            ag.make_session(t, backend="bogus")
            check("unknown backend raises", False, "expected AgentError")
        except ag.AgentError:
            check("unknown backend raises", True)


def test_make_session_lazy_import():
    """Verify that `make_session(backend='claude')` does not import the codex
    module, and vice versa. This matters when only one of the two binaries
    is installed."""
    print("\n[make_session lazy import]")
    # Touch nothing if the binaries aren't on PATH; just check import laziness
    # by inspecting sys.modules before/after a known-bogus call.
    before = set(sys.modules.keys())
    try:
        ag.make_session("/tmp", backend="bogus")
    except ag.AgentError:
        pass
    after = set(sys.modules.keys())
    new = after - before
    check("no backend module imported on bogus call",
          not any("agent_session.claude" in m or "agent_session.codex" in m
                  for m in new),
          f"new modules: {new}")


def test_constants_consistent():
    print("\n[constants consistency]")
    eq("BACKEND_CLAUDE in ALL_BACKENDS", ag.BACKEND_CLAUDE in ag.ALL_BACKENDS, True)
    eq("BACKEND_CODEX in ALL_BACKENDS", ag.BACKEND_CODEX in ag.ALL_BACKENDS, True)
    eq("ALL_BACKENDS has 2 entries", len(ag.ALL_BACKENDS), 2)
    eq("SANDBOX_READ_ONLY in ALL_SANDBOXES", ag.SANDBOX_READ_ONLY in ag.ALL_SANDBOXES, True)
    eq("ALL_SANDBOXES has 3 entries", len(ag.ALL_SANDBOXES), 3)


def test_AgentResult_default_fields():
    print("\n[AgentResult dataclass defaults]")
    r = ag.AgentResult(
        ok=True, exit_code=0, stdout="", stderr="",
        final_message="hi", duration_sec=1.2,
    )
    eq("files_created default empty", r.files_created, [])
    eq("files_modified default empty", r.files_modified, [])
    eq("session_id default None", r.session_id, None)
    eq("backend default empty", r.backend, "")


def main() -> int:
    test_public_api_exports()
    test_pick_backend_random_in_range()
    test_pick_backend_env_override()
    test_pick_backend_no_env_unset_falls_through()
    test_make_session_unknown_backend_raises()
    test_make_session_lazy_import()
    test_constants_consistent()
    test_AgentResult_default_fields()

    if _failures:
        print(f"\n--- {len(_failures)} smoke test(s) FAILED ---")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\n--- all smoke tests passed ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
