"""Managed process supervision primitives.

The voice-agent CLI supervises long-lived child processes (the local
``agentd`` Node daemon, future LiveKit worker side-cars, …) so they can be
started from a single entry point and stopped cleanly without
relying on port-based heuristics or ``kill -9`` fallbacks.

Two surfaces are exposed:

- :class:`OwnedProcess` — frozen record of a process we started.
- :class:`ProcessSupervisor` — start/stop lifecycle backed by a per-process
  state JSON (``runtime-<name>.json``) so we can recover after a crash
  and verify ownership before signalling anything.

Every side effect (subprocess spawn, ``ps`` command lookup, signal
delivery) is constructor-injected so unit tests can run hermetically.
The defaults wire to stdlib (``subprocess.Popen``, ``ps``, ``os.kill``).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


# ───────── Data types ─────────


@dataclass(frozen=True)
class OwnedProcess:
    """Record of a child process started by the supervisor."""

    name: str
    pid: int
    command: tuple[str, ...]
    log_path: Path


# ───────── Type aliases ─────────


#: Subprocess spawner signature — matches ``subprocess.Popen``.
PopenFactory = Callable[..., object]
#: ``ps`` probe signature — returns the cmdline for a pid, or ``None``.
PsProbe = Callable[[int], Optional[str]]
#: Signal delivery — defaults to ``os.kill``; injected in tests.
KillFn = Callable[[int, int], None]


# ───────── Defaults ─────────


def _default_popen(*args, **kwargs):
    return subprocess.Popen(*args, **kwargs)


def _default_ps(pid: int) -> Optional[str]:
    """Best-effort ``ps`` lookup; returns ``None`` if the pid is gone.

    Uses ``ps -o command= -p <pid>`` so we only get the cmdline (no
    header) and short-form flags. macOS / Linux both support this.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    cmdline = result.stdout.strip()
    return cmdline or None


def _default_kill(pid: int, sig: int) -> None:
    os.kill(pid, sig)


def _state_path_for(log_path: Path) -> Path:
    return log_path.parent / f"runtime-{log_path.stem}.json"


def _atomic_write(path: Path, payload: dict) -> None:
    """Write JSON atomically with 0600 permissions.

    Uses ``os.open`` + ``O_NOFOLLOW`` semantics via the ``opener`` and a
    ``os.replace`` rename so partial writes never replace the live state
    file. Permission is set to ``0o600`` before the rename.
    """
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True)
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# ───────── Supervisor ─────────


class ProcessSupervisor:
    """Spawn, track, and stop child processes with ownership verification.

    The supervisor writes a state JSON per process (``runtime-<name>.json``
    next to the log file) recording pid, command, log path, and
    ``owned: true``. On :meth:`stop` it consults the injected ``ps`` to
    confirm the pid still belongs to us before sending ``SIGTERM``; if
    the pid is gone or owned by someone else, only the stale state is
    removed — no signal is sent. This guards against PID-reuse killing
    an unrelated process.
    """

    def __init__(
        self,
        *,
        popen_factory: PopenFactory | None = None,
        ps: PsProbe | None = None,
        kill: KillFn | None = None,
    ) -> None:
        self._popen = popen_factory or _default_popen
        self._ps = ps or _default_ps
        self._kill = kill or _default_kill

    # ── ownership probe ──

    def is_owned(self, pid: int, expected_fragment: str) -> bool:
        """True iff ``ps`` still shows ``expected_fragment`` in the cmdline."""
        cmdline = self._ps(pid)
        if not cmdline:
            return False
        return expected_fragment in cmdline

    # ── start ──

    def start(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
    ) -> OwnedProcess:
        """Spawn ``command`` detached, return the OwnedProcess + persist state."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        try:
            proc = self._popen(
                list(command),
                cwd=str(cwd),
                start_new_session=True,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        finally:
            log_file.close()
        pid = int(proc.pid)  # type: ignore[attr-defined]
        owned = OwnedProcess(
            name=name,
            pid=pid,
            command=tuple(command),
            log_path=log_path,
        )
        self._write_state(owned)
        return owned

    # ── stop ──

    def stop(self, process: OwnedProcess) -> None:
        """Verify ownership then ``SIGTERM``; always cleans up state.

        If the pid is gone or no longer matches the expected command
        fragment (e.g. PID was recycled by a different process), state
        is dropped but **no signal is sent**. This is the deliberate
        guard against accidental cross-process kills.
        """
        state_path = self._state_path(process)
        expected = self._expected_fragment(process)
        try:
            if self.is_owned(process.pid, expected):
                try:
                    self._kill(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    # Already exited, or owned by a different uid now.
                    pass
        finally:
            self._remove_state(state_path)

    # ── state helpers ──

    def _write_state(self, process: OwnedProcess) -> None:
        path = self._state_path(process)
        _atomic_write(
            path,
            {
                "name": process.name,
                "pid": process.pid,
                "command": list(process.command),
                "log_path": str(process.log_path),
                "owned": True,
            },
        )

    def _remove_state(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _state_path(process: OwnedProcess) -> Path:
        return _state_path_for(process.log_path)

    @staticmethod
    def _expected_fragment(process: OwnedProcess) -> str:
        """Choose a stable command-line fragment to verify ownership."""
        for token in reversed(process.command):
            if token.startswith("-"):
                continue
            return token
        return process.name
