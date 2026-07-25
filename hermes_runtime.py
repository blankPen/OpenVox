"""Hermes CLI / HTTP readiness and one-shot setup.

The voice-agent worker talks to a locally-running Hermes CLI to drive the
gateway and its OpenAI-compatible API server. This module wraps the
external ``hermes`` CLI behind dependency-injected callables so unit
tests can fake everything (path lookup, subprocess, HTTP GET, clock).

Two surfaces are exposed:

- :class:`HermesRuntime` — probes the local gateway and returns a
  :class:`HermesReadiness` snapshot. Optional ``start()`` /
  ``wait_until_ready()`` / ``ensure_ready()`` drive the lifecycle.
- :class:`HermesConfigurator` — writes the API server block through
  ``hermes config set`` plus ``hermes gateway restart``. A timestamped
  backup of ``~/.hermes/config.yaml`` is taken before the first write
  and restored on any failure.

Only the Python standard library is used (``urllib``, ``subprocess``,
``shutil``, ``tempfile``, ``pathlib``).
"""
from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal


# ───────── Data types ─────────


@dataclass(frozen=True)
class HermesConfig:
    """Connection settings for the local Hermes gateway.

    All defaults are aligned with ``~/.hermes/config.yaml`` produced by
    a fresh ``hermes init`` run.
    """

    cli: str
    api_base: str
    api_key: str
    host: str = "127.0.0.1"
    port: int = 8642
    startup_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class HttpResult:
    """Small HTTP response wrapper used by the injectable getter."""

    status: int
    body: bytes


@dataclass(frozen=True)
class HermesReadiness:
    """Snapshot of the local Hermes gateway's reachability.

    ``status`` is one of five stable buckets — any error funneled
    through :meth:`HermesRuntime.inspect` ends up in exactly one of
    these. ``detail`` carries a human-readable hint but must never echo
    the bearer token (see the test that asserts this).
    """

    status: Literal[
        "ready", "cli_missing", "cli_error", "http_unavailable", "api_unavailable"
    ]
    cli_path: str | None
    cli_version: str | None
    health_url: str
    detail: str

    @property
    def ready(self) -> bool:
        return self.status == "ready"


# ───────── Errors ─────────


class HermesSetupError(RuntimeError):
    """Raised when a configuration write fails and we couldn't recover.

    :class:`HermesConfigurator` always rolls the target YAML file back
    from the timestamped backup before re-raising, so this error means
    ``~/.hermes/config.yaml`` is in its pre-apply state.
    """


# ───────── Type aliases ─────────


#: Subprocess runner signature — matches ``subprocess.run``.
ProcessRunner = Callable[..., Any]
#: HTTP getter signature — ``(url, headers, timeout) -> HttpResult``.
HttpGetter = Callable[[str, "dict | None", float], HttpResult]


# ───────── Defaults ─────────


def _default_which(name: str) -> str | None:
    return shutil.which(name)


def _default_run(argv, *, timeout, check=False, cwd=None):
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        cwd=cwd,
    )


def _default_http_get(url: str, headers: "dict | None", timeout: float) -> HttpResult:
    """Default HTTP getter using ``urllib.request``.

    Both transport errors (network unreachable, DNS, timeout) and 4xx/5xx
    responses are normalised to :class:`HttpResult` so callers can branch
    on ``status`` instead of catching exceptions.
    """
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return HttpResult(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:  # pragma: no cover — defensive
            pass
        return HttpResult(status=exc.code, body=body)


# ───────── HermesRuntime ─────────


class HermesRuntime:
    """Probe the local Hermes CLI + HTTP gateway.

    All side-effecting callables (``which``, ``run``, ``get``, ``sleep``,
    ``clock``) are constructor-injected so tests can run hermetically.
    The defaults wire to stdlib (``shutil.which``, ``subprocess.run``,
    ``urllib.request``, ``time.sleep``, ``time.monotonic``).
    """

    def __init__(
        self,
        config: HermesConfig,
        *,
        which: "Callable[[str], str | None] | None" = None,
        run: ProcessRunner | None = None,
        get: HttpGetter | None = None,
        sleep: "Callable[[float], None] | None" = None,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        self._config = config
        self._which = which or _default_which
        self._run = run or _default_run
        self._get = get or _default_http_get
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    # ── URL helpers ──

    def _health_url(self) -> str:
        base = self._config.api_base.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base + "/health"

    def _models_url(self) -> str:
        return self._config.api_base.rstrip("/") + "/models"

    def _auth_headers(self) -> "dict | None":
        if not self._config.api_key:
            return None
        return {"Authorization": f"Bearer {self._config.api_key}"}

    # ── Lifecycle ──

    def inspect(self) -> HermesReadiness:
        """Probe CLI + HTTP in one pass and return a stable readiness snapshot."""
        cfg = self._config
        health_url = self._health_url()
        cli_path = self._which(cfg.cli)
        if cli_path is None:
            return HermesReadiness(
                status="cli_missing",
                cli_path=None,
                cli_version=None,
                health_url=health_url,
                detail=f"{cfg.cli} not found in PATH",
            )

        # 1. CLI version probe — fail-soft to cli_error.
        try:
            version_result = self._run([cfg.cli, "--version"], timeout=5.0)
        except (FileNotFoundError, OSError) as exc:
            return HermesReadiness(
                status="cli_error",
                cli_path=cli_path,
                cli_version=None,
                health_url=health_url,
                detail=f"{cfg.cli} --version failed: {exc}",
            )
        if version_result.returncode != 0:
            return HermesReadiness(
                status="cli_error",
                cli_path=cli_path,
                cli_version=None,
                health_url=health_url,
                detail=f"{cfg.cli} --version exited {version_result.returncode}",
            )
        cli_version = _first_line(version_result.stdout)

        # 2. HTTP health probe — anonymous.
        try:
            health_result = self._get(health_url, None, 3.0)
        except (urllib.error.URLError, OSError) as exc:
            return HermesReadiness(
                status="http_unavailable",
                cli_path=cli_path,
                cli_version=cli_version,
                health_url=health_url,
                detail=f"health probe failed: {exc}",
            )
        if health_result.status != 200:
            return HermesReadiness(
                status="http_unavailable",
                cli_path=cli_path,
                cli_version=cli_version,
                health_url=health_url,
                detail=f"health probe returned {health_result.status}",
            )

        # 3. Authenticated /v1/models probe — validates bearer token.
        try:
            models_result = self._get(self._models_url(), self._auth_headers(), 3.0)
        except (urllib.error.URLError, OSError) as exc:
            return HermesReadiness(
                status="api_unavailable",
                cli_path=cli_path,
                cli_version=cli_version,
                health_url=health_url,
                detail=f"models probe failed: {exc}",
            )
        if models_result.status != 200:
            return HermesReadiness(
                status="api_unavailable",
                cli_path=cli_path,
                cli_version=cli_version,
                health_url=health_url,
                detail=f"models probe returned {models_result.status}",
            )

        return HermesReadiness(
            status="ready",
            cli_path=cli_path,
            cli_version=cli_version,
            health_url=health_url,
            detail="ok",
        )

    def start(self) -> None:
        """Run ``<cli> gateway start`` — the official foreground/daemon command."""
        self._run([self._config.cli, "gateway", "start"], timeout=10.0)

    def wait_until_ready(self) -> HermesReadiness:
        """Poll :meth:`inspect` every 250ms until ``ready`` or the timeout expires."""
        deadline = self._clock() + self._config.startup_timeout_seconds
        last = self.inspect()
        while not last.ready and self._clock() < deadline:
            self._sleep(0.25)
            last = self.inspect()
        return last

    def ensure_ready(self, *, auto_start: bool = False) -> HermesReadiness:
        """Inspect-only by default; with ``auto_start=True`` start and poll.

        The default (``auto_start=False``) is the **non-interactive**
        semantic — no subprocess is invoked beyond the inspect-time
        ``--version`` probe. ``auto_start=True`` is reserved for
        non-interactive flows that already have user opt-in (e.g. a CLI
        ``--yes`` flag).
        """
        if not auto_start:
            return self.inspect()
        self.start()
        return self.wait_until_ready()

    def setup_api_server(self, *, api_key: str, apply: bool) -> None:
        """Drive :class:`HermesConfigurator` against the default ``~/.hermes/config.yaml``."""
        HermesConfigurator(
            cli=self._config.cli,
            host=self._config.host,
            port=self._config.port,
            config_path=Path("~/.hermes/config.yaml").expanduser(),
            run=self._run,
        ).apply(api_key=api_key, apply=apply)


# ───────── HermesConfigurator ─────────


def _backup_path(config_path: Path) -> Path:
    """Return a timestamped sibling path: ``config.yaml.bak.YYYYMMDDHHMMSS``."""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return config_path.with_name(f"{config_path.name}.bak.{ts}")


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    return text.strip().splitlines()[0] if text.strip() else None


class HermesConfigurator:
    """Apply API server settings through the official ``hermes`` CLI.

    Sequence (when ``apply=True``):

    1. Snapshot ``config_path`` to ``<name>.bak.YYYYMMDDHHMMSS``.
    2. Run ``hermes config set …`` for the four API server keys.
    3. Run ``hermes gateway restart``.
    4. On any failure, restore from the backup and raise
       :class:`HermesSetupError`.

    When ``apply=False`` the call is a pure preview: the command list
    is returned, no runner is invoked, no file is touched.
    """

    def __init__(
        self,
        *,
        cli: str,
        host: str,
        port: int,
        config_path: "Path | str",
        run: ProcessRunner | None = None,
        http_get: HttpGetter | None = None,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        self._cli = cli
        self._host = host
        self._port = port
        self._config_path = Path(config_path)
        self._run = run or _default_run
        self._http_get = http_get or _default_http_get
        self._clock = clock or time.monotonic

    # ── Command list ──

    def _commands(self, api_key: str) -> "list[list[str]]":
        cli = self._cli
        return [
            [cli, "config", "set", "platforms.api_server.enabled", "true"],
            [cli, "config", "set", "platforms.api_server.extra.host", self._host],
            [cli, "config", "set", "platforms.api_server.extra.port", str(self._port)],
            [cli, "config", "set", "platforms.api_server.extra.key", api_key],
            [cli, "gateway", "restart"],
        ]

    # ── Public API ──

    def apply(self, *, api_key: str, apply: bool = True) -> "list[list[str]] | None":
        """Apply or preview the configuration.

        - ``apply=True`` → execute the command sequence with backup /
          restore semantics; raises :class:`HermesSetupError` on any
          failure. Returns ``None`` on success.
        - ``apply=False`` → return the command list, never touch disk
          or invoke the runner.
        """
        commands = self._commands(api_key)
        if not apply:
            return [list(cmd) for cmd in commands]

        config_path = self._config_path
        backup = _backup_path(config_path)
        if config_path.exists():
            backup.write_bytes(config_path.read_bytes())

        try:
            for cmd in commands:
                result = self._run(cmd, timeout=10.0)
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    raise HermesSetupError(
                        f"hermes command failed: {' '.join(cmd[:3])}… "
                        f"(rc={result.returncode}): {stderr}"
                    )
        except BaseException:
            if backup.exists():
                config_path.write_bytes(backup.read_bytes())
            raise

        return None
