"""Unit tests for process_runtime.py.

Every side effect (subprocess spawn, ``ps`` inspection, signal delivery) is
injected through the :class:`ProcessSupervisor` constructor so these tests run
hermetically — no real Node process is ever spawned and no signal is ever sent
to a live PID.
"""
from __future__ import annotations

import json
import os
import signal
import stat
from pathlib import Path

from openvox_worker.process_runtime import OwnedProcess, ProcessSupervisor


# ───────── Fakes ─────────


class FakePopen:
    """Callable stand-in for ``subprocess.Popen`` — records the last call."""

    def __init__(self, *, pid: int):
        self.pid = pid
        self.calls: list[tuple] = []

    def __call__(self, command, **kwargs):
        self.calls.append((tuple(command), kwargs))
        return self


def ps_returning(cmdline: str | None):
    """Injectable ``ps`` returning a fixed command line for any pid."""

    def ps(pid: int):
        return cmdline

    return ps


def recording_kill():
    sent: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    return kill, sent


# ───────── Step 1: start records pid + command + state ─────────


def test_start_records_pid_and_command(tmp_path):
    popen = FakePopen(pid=4321)
    supervisor = ProcessSupervisor(popen_factory=popen)
    owned = supervisor.start(
        "agentd",
        ["node", "dist/index.js"],
        cwd=tmp_path,
        log_path=tmp_path / "agentd.log",
    )
    assert owned.pid == 4321
    assert owned.command == ("node", "dist/index.js")
    assert (tmp_path / "runtime-agentd.json").exists()


def test_start_spawns_detached_with_logfile(tmp_path):
    popen = FakePopen(pid=99)
    supervisor = ProcessSupervisor(popen_factory=popen)
    supervisor.start(
        "agentd",
        ["node", "dist/index.js"],
        cwd=tmp_path,
        log_path=tmp_path / "agentd.log",
    )
    (command, kwargs) = popen.calls[-1]
    assert command == ("node", "dist/index.js")
    assert kwargs["start_new_session"] is True
    assert Path(kwargs["cwd"]) == tmp_path


def test_start_state_json_is_0600_and_owned(tmp_path):
    popen = FakePopen(pid=4321)
    supervisor = ProcessSupervisor(popen_factory=popen)
    supervisor.start(
        "agentd",
        ["node", "dist/index.js"],
        cwd=tmp_path,
        log_path=tmp_path / "agentd.log",
    )
    state_path = tmp_path / "runtime-agentd.json"
    data = json.loads(state_path.read_text())
    assert data["pid"] == 4321
    assert data["command"] == ["node", "dist/index.js"]
    assert data["owned"] is True
    mode = stat.S_IMODE(os.stat(state_path).st_mode)
    assert mode == 0o600


# ───────── is_owned ─────────


def test_is_owned_true_when_fragment_present():
    supervisor = ProcessSupervisor(
        popen_factory=FakePopen(pid=1),
        ps=ps_returning("node /repo/apps/agentd/dist/index.js --config x"),
    )
    assert supervisor.is_owned(4321, "apps/agentd/dist/index.js") is True


def test_is_owned_false_when_fragment_missing():
    supervisor = ProcessSupervisor(
        popen_factory=FakePopen(pid=1),
        ps=ps_returning("/usr/bin/postgres -D /data"),
    )
    assert supervisor.is_owned(4321, "apps/agentd/dist/index.js") is False


def test_is_owned_false_when_pid_gone():
    supervisor = ProcessSupervisor(
        popen_factory=FakePopen(pid=1),
        ps=ps_returning(None),
    )
    assert supervisor.is_owned(4321, "apps/agentd/dist/index.js") is False


# ───────── stop: only kill verified owned PIDs ─────────


def test_stop_sends_sigterm_when_owned(tmp_path):
    popen = FakePopen(pid=4321)
    kill, sent = recording_kill()
    supervisor = ProcessSupervisor(
        popen_factory=popen,
        ps=ps_returning("node /repo/apps/agentd/dist/index.js --config x"),
        kill=kill,
    )
    owned = supervisor.start(
        "agentd",
        ["node", "/repo/apps/agentd/dist/index.js", "--config", "x"],
        cwd=tmp_path,
        log_path=tmp_path / "agentd.log",
    )
    supervisor.stop(owned)
    assert sent == [(4321, signal.SIGTERM)]
    # state json removed after stop
    assert not (tmp_path / "runtime-agentd.json").exists()


def test_stop_does_not_kill_stale_pid(tmp_path):
    """PID reuse guard: if ps no longer shows our command, delete state, no kill."""
    popen = FakePopen(pid=4321)
    kill, sent = recording_kill()
    supervisor = ProcessSupervisor(
        popen_factory=popen,
        ps=ps_returning("/usr/bin/some-other-daemon"),  # PID reused by unrelated proc
        kill=kill,
    )
    owned = supervisor.start(
        "agentd",
        ["node", "/repo/apps/agentd/dist/index.js", "--config", "x"],
        cwd=tmp_path,
        log_path=tmp_path / "agentd.log",
    )
    supervisor.stop(owned)
    assert sent == []  # never kill an unverified PID
    assert not (tmp_path / "runtime-agentd.json").exists()


def test_stop_no_state_is_noop(tmp_path):
    kill, sent = recording_kill()
    supervisor = ProcessSupervisor(
        popen_factory=FakePopen(pid=1),
        ps=ps_returning(None),
        kill=kill,
    )
    owned = OwnedProcess(
        name="agentd",
        pid=4321,
        command=("node", "/repo/apps/agentd/dist/index.js"),
        log_path=tmp_path / "agentd.log",
    )
    # No state file was ever written; stop must be a safe no-op.
    supervisor.stop(owned)
    assert sent == []
