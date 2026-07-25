"""Supervised lifecycle for the local ``agentd`` Node daemon.

The voice-agent CLI depends on a running ``agentd`` instance to serve the
OpenAI-compatible ``/v1/chat/completions`` and ``/v1/models`` endpoints.
:class:`AgentdRuntime` wires the configuration projection, the
supervised Node process, and the readiness health-check into a single
object that ``main.py`` can call from the unified CLI.

Every external side effect is constructor-injected so unit tests run
without ever touching Node, pnpm, or the network:

- ``which``     — ``shutil.which`` by default
- ``run``       — ``subprocess.run`` by default
- ``http_get``  — ``urllib.request``-based getter by default
- ``sleep``     — ``time.sleep`` by default
- ``clock``     — ``time.monotonic`` by default

The supervisor (see :mod:`process_runtime`) handles spawn, state
persistence, and PID-ownership-verified SIGTERM.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from config import Config
from hermes_runtime import HttpResult
from process_runtime import OwnedProcess, ProcessSupervisor


# ───────── Type aliases ─────────


#: HTTP getter signature — ``(url, headers, timeout) -> HttpResult``.
HttpGetter = Callable[[str, "dict | None", float], HttpResult]
#: Subprocess runner signature — matches ``subprocess.run``.
ProcessRunner = Callable[..., Any]
#: ``shutil.which`` substitute for tests.
Which = Callable[[str], Optional[str]]
#: ``time.sleep`` substitute.
Sleeper = Callable[[float], None]
#: ``time.monotonic`` substitute.
Clock = Callable[[], float]


# ───────── Defaults ─────────


def _default_which(name: str) -> Optional[str]:
    return shutil.which(name)


def _default_run(argv, *, cwd=None, timeout=None, check=False):
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        cwd=cwd,
    )


def _default_http_get(url: str, headers: "dict | None", timeout: float) -> HttpResult:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return HttpResult(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:  # pragma: no cover
            pass
        return HttpResult(status=exc.code, body=body)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomic JSON write at ``0600`` (mirrors ``process_runtime._atomic_write``)."""
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


# ───────── Errors ─────────


class AgentdSetupError(RuntimeError):
    """Raised when agentd cannot be started or fails its health probe."""


# ───────── AgentdRuntime ─────────


@dataclass
class _Projection:
    host: str
    port: int
    api_key: str

    def to_json(self) -> dict:
        tokens = [self.api_key] if self.api_key else []
        return {
            "host": self.host,
            "port": self.port,
            "logLevel": "info",
            "auth": {"tokens": tokens},
            "providers": [],
        }


class AgentdRuntime:
    """Owns the lifecycle of a local ``agentd`` Node process.

    - :meth:`ensure_config` writes the runtime JSON projection.
    - :meth:`start` builds (if needed) and spawns the supervised process,
      then polls ``/health`` and ``/v1/models`` until both return 200 or
      the deadline is hit; on any health failure the just-started owned
      process is stopped before raising.
    - :meth:`stop` re-hydrates an :class:`OwnedProcess` from the
      supervisor's state JSON and hands it back for ownership-verified
      termination.
    - :meth:`status` returns a snapshot (running, pid, log path) without
      re-probing the HTTP endpoints.
    """

    def __init__(
        self,
        *,
        cfg: Config,
        repo_root: Path,
        runtime_dir: Path,
        supervisor: ProcessSupervisor,
        http_get: HttpGetter,
        which: Which | None = None,
        run: ProcessRunner | None = None,
        sleep: Sleeper | None = None,
        clock: Clock | None = None,
        startup_timeout_seconds: float = 10.0,
        poll_attempts: int = 40,
    ) -> None:
        self._cfg = cfg
        self._repo_root = Path(repo_root)
        self._runtime_dir = Path(runtime_dir)
        self._supervisor = supervisor
        self._http_get = http_get
        self._which = which or _default_which
        self._run = run or _default_run
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._startup_timeout = startup_timeout_seconds
        self._poll_attempts = poll_attempts

    # ── paths ──

    @property
    def config_path(self) -> Path:
        return self._runtime_dir / "agentd.json"

    @property
    def state_path(self) -> Path:
        return self._runtime_dir / "runtime-agentd.json"

    @property
    def dist_path(self) -> Path:
        return self._repo_root / "apps" / "agentd" / "dist" / "index.js"

    @property
    def log_path(self) -> Path:
        return self._runtime_dir / "agentd.log"

    # ── config projection ──

    def _projection(self) -> _Projection:
        return _Projection(
            host=self._cfg.get("agentd.host", "127.0.0.1"),
            port=int(self._cfg.get("agentd.port", 8787)),
            api_key=str(self._cfg.get("agentd.api_key", "")),
        )

    def ensure_config(self) -> Path:
        """Atomically write the config projection to ``runtime_dir/agentd.json``."""
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self.config_path, self._projection().to_json())
        return self.config_path

    # ── start ──

    def start(self) -> OwnedProcess:
        """Build (if needed) + spawn the agentd process and wait for /health."""
        self.ensure_config()
        self._ensure_built()
        self._ensure_node()
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        owned = self._supervisor.start(
            "agentd",
            [
                "node",
                str(self.dist_path),
                "--config",
                str(self.config_path),
            ],
            cwd=self._repo_root,
            log_path=self.log_path,
        )
        if not self._wait_until_ready():
            # stop is ownership-verified; if the pid is gone or
            # reassigned, the supervisor will only drop stale state.
            self._supervisor.stop(owned)
            raise AgentdSetupError(
                f"agentd failed health probe at {self._health_url()} "
                f"after {self._poll_attempts} attempts"
            )
        return owned

    def _ensure_node(self) -> None:
        if self._which("node") is None:
            raise AgentdSetupError("node executable not found in PATH")

    def _ensure_built(self) -> None:
        if self.dist_path.exists():
            return
        agentd_dir = self._repo_root / "apps" / "agentd"
        self._run(
            ["pnpm", "--dir", str(agentd_dir), "install", "--frozen-lockfile"],
            cwd=str(agentd_dir),
            timeout=300.0,
            check=False,
        )
        self._run(
            ["pnpm", "--dir", str(agentd_dir), "build"],
            cwd=str(agentd_dir),
            timeout=300.0,
            check=False,
        )
        if not self.dist_path.exists():
            raise AgentdSetupError(
                f"agentd dist not produced at {self.dist_path} after pnpm build"
            )

    # ── health / models probe ──

    def _health_url(self) -> str:
        proj = self._projection()
        return f"http://{proj.host}:{proj.port}/health"

    def _models_url(self) -> str:
        proj = self._projection()
        return f"http://{proj.host}:{proj.port}/v1/models"

    def _auth_headers(self) -> "dict | None":
        api_key = self._projection().api_key
        if not api_key:
            return None
        return {"Authorization": f"Bearer {api_key}"}

    def _wait_until_ready(self) -> bool:
        deadline = self._clock() + self._startup_timeout
        attempt = 0
        while attempt < self._poll_attempts and self._clock() < deadline:
            try:
                health = self._http_get(self._health_url(), None, 3.0)
            except (urllib.error.URLError, OSError):
                health = None
            if health is not None and health.status == 200:
                try:
                    models = self._http_get(
                        self._models_url(), self._auth_headers(), 3.0
                    )
                except (urllib.error.URLError, OSError):
                    models = None
                if models is not None and models.status == 200:
                    return True
            attempt += 1
            self._sleep(0.25)
        return False

    # ── stop ──

    def stop(self) -> None:
        """Reconstruct the OwnedProcess from state JSON and stop it."""
        path = self.state_path
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        owned = OwnedProcess(
            name=payload.get("name", "agentd"),
            pid=int(payload["pid"]),
            command=tuple(payload.get("command", ())),
            log_path=Path(payload.get("log_path", str(self.log_path))),
        )
        self._supervisor.stop(owned)

    # ── status ──

    def status(self) -> dict:
        """Return a status snapshot without re-probing the HTTP endpoint."""
        path = self.state_path
        if not path.exists():
            return {
                "running": False,
                "pid": None,
                "log_path": str(self.log_path),
                "config_path": str(self.config_path),
            }
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"running": False, "pid": None}
        pid = int(payload.get("pid", 0))
        command = payload.get("command", [])
        # Use supervisor's ownership probe to confirm it's still us.
        fragment = command[-1] if command else "agentd"
        owned = self._supervisor.is_owned(pid, fragment)
        return {
            "running": owned,
            "pid": pid if owned else None,
            "command": command,
            "log_path": payload.get("log_path", str(self.log_path)),
            "config_path": str(self.config_path),
        }
