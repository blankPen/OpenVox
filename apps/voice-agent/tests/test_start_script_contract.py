"""Contract tests for ``scripts/start.sh``.

These tests pin the behavioural contract that the legacy launch script
delegates lifecycle management to the unified ``openvox`` CLI rather than
re-implementing provider / port semantics in shell. They are intentionally
file-level (string assertions) so they never touch the host's live LiveKit /
agent processes — the original ``lsof -ti:$WORKER_PORT | xargs kill -9``
snippet was unsafe on shared dev machines.
"""
from __future__ import annotations

from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "start.sh"


def _read_script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_start_script_delegates_to_openvox_cli():
    text = _read_script()
    assert "openvox_worker" in text, (
        "scripts/start.sh must delegate lifecycle to the unified openvox CLI "
        "(see openvox_worker); re-implementing provider logic in shell is "
        "explicitly out of scope."
    )
    assert "kill -9" not in text, (
        "scripts/start.sh must not issue unconditional 'kill -9' commands; "
        "doing so can terminate other users' LiveKit / agent processes on "
        "shared dev hosts."
    )


def test_start_script_avoids_port_based_kill():
    text = _read_script()
    # The original script used `lsof -ti:$WORKER_PORT | xargs kill -9` which
    # was both dangerous (port collision with other users) and obsolete now
    # that ``openvox_worker`` owns the supervised ``agentd`` lifecycle.
    forbidden_tokens = (
        "lsof -ti",
        "xargs kill -9",
        "WORKER_PORT",
    )
    for token in forbidden_tokens:
        assert token not in text, (
            f"scripts/start.sh still contains the obsolete port-kill snippet "
            f"({token!r}); remove it so the script does not assume it owns "
            "the LiveKit IPC port."
        )


def test_start_script_preserves_legacy_subcommands():
    text = _read_script()
    # The compatibility surface (start / fg / stop / status) must keep working
    # for downstream scripts, but each branch should defer to openvox_worker
    # rather than talk to ports or PIDs directly.
    for action in ("start", "fg", "stop", "status"):
        assert action in text, (
            f"scripts/start.sh is missing the legacy {action!r} action; "
            "downstream callers expect the compatibility surface to remain."
        )
    assert text.count("openvox_worker") >= 1


def test_start_script_does_not_require_live_livekit_process():
    """The script must not invoke `lsof` / port probes that would fail on hosts
    with no LiveKit / worker process running.

    The legacy implementation called ``lsof -ti:$WORKER_PORT`` on every action,
    which produced confusing error output (and, worse, ``kill -9`` on shared
    hosts) when no worker was running. The new implementation must rely on
    ``openvox_worker status`` / ``openvox_worker start`` exclusively.
    """
    text = _read_script()
    assert "lsof" not in text, (
        "scripts/start.sh still shells out to lsof; it should delegate all "
        "process / port inspection to openvox_worker status."
    )
