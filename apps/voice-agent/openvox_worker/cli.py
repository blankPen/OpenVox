"""Unified ``openvox`` runtime CLI.

One entry point to configure and operate the voice-agent stack:

- ``openvox init``          — write / update ``~/.openvox/config.json`` and
                              pick the LLM provider (never echoes secrets).
- ``openvox logs [target]`` — view / follow runtime logs (``agentd`` /
  ``worker``) with ``--tail``, ``--since`` and ``--grep`` filters.

- ``openvox start``         — bring up the selected LLM backend (Hermes
                              readiness probe *or* supervised ``agentd``),
                              then launch the LiveKit worker. Any failure
                              reverse-stops the processes this run owns.
- ``openvox stop``          — stop the supervised ``agentd`` process. The
                              external Hermes gateway is never stopped.
- ``openvox status [--json]``— report per-provider status; ``codex`` and
                              ``openclaw`` are surfaced as ``planned``.
- ``openvox doctor hermes`` — print :meth:`HermesRuntime.inspect` output.
- ``openvox hermes setup``  — drive :class:`HermesConfigurator` (preview by
                              default; ``--yes`` applies).

Exit codes: ``0`` success, ``2`` user error (bad config / planned provider
/ invalid args), ``1`` runtime error (backend failed to start).

The orchestration helpers (:func:`orchestrate_start` / :func:`orchestrate_stop`)
take the runtime objects as keyword arguments so unit tests can inject
fakes without touching Node, Hermes, or the network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from rich_argparse import RawDescriptionRichHelpFormatter

from .agentd_runtime import AgentdRuntime, AgentdSetupError, _default_http_get
from .config import Config, ConfigError
from .hermes_runtime import HermesConfig, HermesRuntime, HermesSetupError
from .llm_provider import PlannedProviderError
from .process_runtime import ProcessSupervisor

try:
    import questionary
    _HAVE_QUESTIONARY = True
except ImportError:
    _HAVE_QUESTIONARY = False


# ───────── Provider catalogue ─────────

SUPPORTED_PROVIDERS = ("hermes", "agentd", "claude")
PLANNED_PROVIDERS = ("codex", "openclaw")
ALL_PROVIDERS = SUPPORTED_PROVIDERS + PLANNED_PROVIDERS

#: Providers shown in ``openvox init`` interactive selection. ``agentd`` is
#: intentionally omitted - it is internal infrastructure surfaced through the
#: individual tool names (claude / codex / openclaw).
USER_FACING_PROVIDERS = ("hermes", "claude", "codex", "openclaw")

#: Icons shown next to each provider in the init selection prompt.
STATUS_ICONS = {
    "installed": "✓",
    "not installed": "✗",
    "planned": "⏳",
}

#: Guidance printed when a provider is selected but is not ready.
PROVIDER_GUIDANCE = {
    "hermes": (
        "Hermes CLI not found in PATH.\n"
        "Install it with: pip install hermes"
    ),
    "claude": (
        "Claude Code not found in PATH.\n"
        "Install it from: https://claude.ai/download"
    ),
    "codex": "Codex support is planned but not yet implemented.",
    "openclaw": "OpenClaw support is planned but not yet implemented.",
}


#: CLI-style names that map to backends.
PROVIDER_ALIASES = {
    "claude": "agentd",
    "codex": "agentd",
    "openclaw": "agentd",
}

#: Minimal, editable defaults seeded into config for each provider section.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "hermes": {
        "cli": "hermes",
        "host": "127.0.0.1",
        "port": 8642,
        "api_base": "http://127.0.0.1:8642/v1",
        "api_key": "",
        "model": "hermes-default",
    },
    "agentd": {
        "host": "127.0.0.1",
        "port": 8787,
        "api_base": "http://127.0.0.1:8787/v1",
        "api_key": "",
        "model": "agentd/claude",
    },
    "codex": {},
    "openclaw": {},
}


# ───────── Filesystem helpers ─────────


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomic JSON write at ``0600`` (mirrors ``process_runtime._atomic_write``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
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


def _read_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"config parse error in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config root must be a JSON object")
    return data


def _config_path(config_arg: str | None) -> Path | None:
    return Path(config_arg).expanduser() if config_arg else None


def _load_config(config_arg: str | None) -> Config:
    return Config.load(_config_path(config_arg))


# ───────── init ─────────


def _prompt(
    label: str,
    current: str,
    *,
    password: bool = False,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Single interactive prompt that masks secrets when questionary is available.

    Returns the user's value (or the current value if the prompt was
    skipped / cancelled). Plain ``input()`` fallback echoes password
    values — there's no portable way to hide stdin without a tty.
    """
    if _HAVE_QUESTIONARY:
        ask = questionary.password if password else questionary.text
        default = "" if password else current
        val = ask(label, default=default).ask()
        if val is None:
            return current
        val = val.strip()
        return val or current
    raw = input_fn(
        f"{label} [{'*' * len(current) if password else current}]: "
    ).strip()
    return raw or current


def init_config(
    path: Path,
    *,
    provider: str | None,
    input_fn: Callable[[str], str] = input,
    output: Callable[..., None] = print,
    interactive: bool = False,
) -> Config:
    """Write / update ``path`` selecting ``provider``.

    Existing keys are preserved (deep-safe): only ``llm.provider`` and any
    *missing* keys in the provider's section are filled from
    :data:`PROVIDER_DEFAULTS`.

    When ``interactive=True`` and no ``--provider`` flag is given, the user is
    presented with a status-aware prompt. Selecting a provider that is not
    installed or is still planned prints guidance and raises :class:`ConfigError`
    so a broken config is never written.
    """
    data = _read_existing(path)
    detected = _detect_providers()
    if provider is None:
        if interactive:
            candidates = [(p, detected[p]) for p in USER_FACING_PROVIDERS]
            if _HAVE_QUESTIONARY:
                choices = [
                    questionary.Choice(
                        title=f"{STATUS_ICONS[info['status']]} {info['label']}",
                        value=p,
                    )
                    for p, info in candidates
                ]
                provider = questionary.select("Select LLM provider", choices=choices).ask()
            else:
                for i, (p, info) in enumerate(candidates, 1):
                    icon = STATUS_ICONS[info["status"]]
                    print(f"  [{i}] {icon} {info['label']} ({info['status']})")
                raw = input_fn("select provider [1]: ").strip()
                idx = int(raw) - 1 if raw.isdigit() else 0
                if idx < 0 or idx >= len(candidates):
                    idx = 0
                provider = candidates[idx][0]
        else:
            entered = input_fn("LLM provider [hermes]: ").strip()
            provider = entered or "hermes"

    original_provider = provider
    # Resolve CLI-style names (claude → agentd, etc.)
    provider = PROVIDER_ALIASES.get(provider, provider)
    if provider not in ALL_PROVIDERS:
        raise ConfigError("unknown llm provider")

    # Surface guidance before writing a config that cannot run.
    if original_provider in detected and detected[original_provider]["status"] != "installed":
        guidance = PROVIDER_GUIDANCE.get(original_provider)
        if guidance:
            output(guidance)
        raise ConfigError(
            f"llm provider {original_provider} is {detected[original_provider]['status']}"
        )

    llm = data.setdefault("llm", {})
    llm["provider"] = provider

    # Write defaults under the backend section (e.g. agentd.*), not tool name.
    backend = PROVIDER_ALIASES.get(provider, provider)
    section = data.setdefault(backend, {})
    for key, value in PROVIDER_DEFAULTS.get(backend, {}).items():
        section.setdefault(key, value)

    # Seed LiveKit connection defaults; prompt in interactive mode.
    livekit_sec = data.setdefault("livekit", {})
    livekit_sec.setdefault("agent_name", "openz")  # internal, not prompted
    if interactive:
        for lk_key, lk_label, lk_default in (
            ("url", "LiveKit server URL", "wss://livekit.openz.top"),
            ("api_key", "LiveKit API key", "devkey"),
            ("api_secret", "LiveKit API secret", "secret"),
        ):
            current = livekit_sec.get(lk_key, lk_default)
            val = _prompt(
                lk_label, current,
                password=(lk_key == "api_secret"),
                input_fn=input_fn,
            )
            if val is not None:
                livekit_sec[lk_key] = val
    else:
        for lk_key, lk_default in (
            ("url", "wss://livekit.openz.top"),
            ("api_key", "devkey"),
            ("api_secret", "secret"),
        ):
            livekit_sec.setdefault(lk_key, lk_default)

    # Volcengine STT/TTS credentials. The worker cannot run without these.
    volc_sec = data.setdefault("volcengine", {})
    if interactive:
        for kind in ("stt", "tts"):
            kind_sec = volc_sec.setdefault(kind, {})
            for vkey, vlabel in (
                ("app_id", f"Volcengine {kind.upper()} app_id"),
                ("access_token", f"Volcengine {kind.upper()} access_token"),
            ):
                current = kind_sec.get(vkey, "")
                val = _prompt(
                    vlabel, current,
                    password=(vkey == "access_token"),
                    input_fn=input_fn,
                )
                # Always write the key (even when empty) so downstream code
                # can rely on the section existing in the config file.
                kind_sec[vkey] = val
    # Non-interactive init never seeds dummy Volcengine credentials — the
    # caller is expected to migrate from .env or set them later.

    _atomic_write_json(path, data)
    output(f"wrote runtime config for provider={provider} -> {path}")
    if interactive:
        output("")
        output("next steps:")
        output(f"  1. review:  cat {path}")
        output("  2. verify:  openvox doctor")
        output("  3. launch:  openvox start")
    return Config(data)


def _detect_providers() -> dict:
    """Scan PATH for available LLM tools and return {provider: {label, status}}.

    hermes is detected directly. claude, codex, openclaw are presented as
    individual tools; agentd is internal and is not surfaced here.
    """
    found = {}
    if shutil.which("hermes") is not None:
        found["hermes"] = {"label": "Hermes (local gateway)", "status": "installed"}
    else:
        found["hermes"] = {"label": "Hermes (local gateway)", "status": "not installed"}

    if shutil.which("claude") is not None:
        found["claude"] = {"label": "Claude Code", "status": "installed"}
    else:
        found["claude"] = {"label": "Claude Code", "status": "not installed"}

    found["codex"] = {"label": "Codex", "status": "planned"}
    found["openclaw"] = {"label": "OpenClaw", "status": "planned"}
    return found


# ───────── Runtime construction (real dependencies) ─────────


def _repo_root() -> Path:
    # cli.py -> apps/voice-agent/openvox_worker/ -> parents[3] == repository root.
    return Path(__file__).resolve().parents[3]


def _runtime_dir() -> Path:
    return Path(os.environ.get("OPENVOX_RUNTIME", "~/.openvox/runtime")).expanduser()


def _build_hermes(cfg: Config) -> HermesRuntime:
    return HermesRuntime(
        HermesConfig(
            cli=str(cfg.get("hermes.cli", "hermes")),
            api_base=str(cfg.get("hermes.api_base", "http://127.0.0.1:8642/v1")),
            api_key=str(cfg.get("hermes.api_key", "")),
            host=str(cfg.get("hermes.host", "127.0.0.1")),
            port=int(cfg.get("hermes.port", 8642)),
        )
    )


def _build_agentd(cfg: Config) -> AgentdRuntime:
    return AgentdRuntime(
        cfg=cfg,
        repo_root=_repo_root(),
        runtime_dir=_runtime_dir(),
        supervisor=ProcessSupervisor(),
        http_get=_default_http_get,
    )


class _WorkerLauncher:
    """Launch the LiveKit worker (``python -m openvox_worker.main``) in foreground.

    Kept intentionally thin — it shells out to ``python -m openvox_worker.main``
    so this module never imports the heavy ``livekit`` / ``openai`` stack. Tests
    inject a fake with the same ``start()`` surface.
    """

    def __init__(
        self,
        *,
        config_path: Path | None,
        livekit_env: dict[str, str] | None = None,
        worker_log_path: Path | None = None,
    ) -> None:
        self._config_path = config_path
        self._livekit_env = livekit_env or {}
        self._worker_log_path = worker_log_path

    def start(self) -> None:
        env = dict(os.environ)
        if self._config_path is not None:
            env["OPENVOX_CONFIG"] = str(self._config_path)
        # Inject LiveKit connection settings from config (overrides env).
        env.update(self._livekit_env)
        argv = [sys.executable, "-m", "openvox_worker.main", "start"]
        if self._worker_log_path is None:
            result = subprocess.run(argv, env=env)
        else:
            self._worker_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = self._worker_log_path.open("ab")
            try:
                proc = subprocess.Popen(
                    argv,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                assert proc.stdout is not None
                with proc.stdout:
                    for chunk in iter(proc.stdout.readline, b""):
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                        log_file.write(chunk)
                    log_file.flush()
                rc = proc.wait()
                result = subprocess.CompletedProcess(argv, rc)
            finally:
                log_file.close()
        if result.returncode != 0:
            raise RuntimeError(f"livekit worker exited with code {result.returncode}")


# ───────── Orchestration ─────────


def _resolve_backend(provider: str) -> str:
    """Map a user-facing provider name to a runtime backend type."""
    return PROVIDER_ALIASES.get(provider, provider)


def _probe_llm_connectivity(settings: Any, *, timeout: float = 10.0) -> tuple[bool, str]:
    """Send a minimal /v1/chat/completions request to verify the LLM is reachable.

    Returns ``(ok, detail)``. ``detail`` is human-friendly status, used by
    the start summary; it never includes the request body or token.
    """
    import urllib.error
    import urllib.request
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    body = json.dumps(
        {
            "model": settings.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode("utf-8")
    url = f"{settings.api_base.rstrip('/')}/chat/completions"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, f"200 OK ({settings.model})"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}"


def _print_start_summary(cfg: Config, *, backend: str, hermes: Any, agentd: Any) -> None:
    """Print a one-shot 'backend ready' summary before the worker takes over."""
    from .llm_provider import resolve_llm_settings
    print("", flush=True)
    if backend == "agentd":
        state = agentd.status()
        loaded = agentd.loaded_providers() if hasattr(agentd, "loaded_providers") else []
        tools = ",".join(_short_tool(t) for t in loaded) or "-"
        pid = state.get("pid") or "-"
        print(f"  ✓ backend ready (agentd)   pid={pid}   loaded=[{tools}]", flush=True)
    else:
        readiness = hermes.inspect()
        status = "ready" if readiness.ready else "not ready"
        print(
            f"  ✓ backend ready (hermes)   status={status}   health={readiness.health_url}",
            flush=True,
        )
    lk_url = cfg.get("livekit.url", "")
    agent_name = cfg.get("livekit.agent_name", "")
    print(f"  ✓ livekit                 url={lk_url}   agent_name={agent_name}", flush=True)
    runtime_log = _runtime_dir() / "worker.log"
    print(f"  ✓ logs                    tail -f {runtime_log}", flush=True)
    # Print resolved LLM target so the user can confirm what's wired.
    try:
        settings = resolve_llm_settings(cfg)
        # Connectivity probe — small /v1/chat/completions round-trip.
        ok, detail = _probe_llm_connectivity(settings)
        if ok:
            print(
                f"  ✓ llm probe               {detail}   via {settings.api_base}",
                flush=True,
            )
        else:
            print(
                f"  ✗ llm probe               failed ({detail}) — worker will retry",
                flush=True,
            )
    except Exception:  # noqa: BLE001 — summary is best-effort
        pass
    print("", flush=True)


def orchestrate_start(
    cfg: Config,
    *,
    hermes: Any,
    agentd: Any,
    worker: Any,
    auto_start: bool = False,
) -> int:
    """Bring up the selected backend then launch the worker.

    Order is fixed: resolve provider → backend → Hermes readiness or
    agentd start → worker launch. Any exception reverse-stops the processes
    this call owns (only ``agentd`` is owned; the external Hermes gateway is
    never stopped) before re-raising.
    """
    provider = cfg.get("llm.provider", "hermes")
    backend = _resolve_backend(provider)
    if provider in PLANNED_PROVIDERS:
        raise PlannedProviderError(
            f"llm provider {provider} is planned, not yet supported"
        )

    owned: list[Any] = []
    try:
        if backend not in ("hermes", "agentd"):
            raise ConfigError("unknown llm provider")
        if backend == "hermes":
            readiness = hermes.ensure_ready(auto_start=auto_start)
            if not readiness.ready:
                raise HermesSetupError(
                    f"hermes gateway not ready: {readiness.status} ({readiness.detail})"
                )
        else:  # agentd (for claude, codex, openclaw)
            agentd.start()
            owned.append(agentd)
        _print_start_summary(cfg, backend=backend, hermes=hermes, agentd=agentd)
        worker.start()
    except BaseException:
        for rt in reversed(owned):
            try:
                rt.stop()
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
        raise
    return 0


def orchestrate_stop(*, hermes: Any, agentd: Any) -> int:
    """Stop the supervised ``agentd`` process; never touch the Hermes gateway."""
    agentd.stop()
    return 0


def collect_status(cfg: Config, *, hermes: Any, agentd: Any) -> dict:
    """Assemble a structured, secret-free status payload.

    Returns ``{selected, livekit, backend, tools}``. ``selected.tool`` is
    the user-facing tool name derived from ``llm.provider`` and
    ``agentd.model`` (e.g. ``agentd/claude`` -> ``claude``). ``backend``
    is the runtime state of whatever backend is currently selected —
    Hermes health probe for the Hermes backend, agentd process + loaded
    models for the agentd bridge. ``tools`` is the catalogue of other
    user-facing tools, filtered so the active one is not repeated.
    """
    selected_backend = cfg.get("llm.provider", "hermes")
    user_tool = _user_facing_tool(cfg)
    livekit = {
        "url": str(cfg.get("livekit.url", "")),
        "agent_name": str(cfg.get("livekit.agent_name", "")),
    }

    backend: dict = {}
    if selected_backend == "hermes":
        readiness = hermes.inspect()
        backend = {
            "kind": "hermes",
            "ready": readiness.ready,
            "status": "ready" if readiness.ready else "not ready",
            "url": readiness.health_url,
            "detail": readiness.detail,
        }
    elif selected_backend == "agentd":
        agentd_state = agentd.status()
        loaded = agentd.loaded_providers() or []
        backend = {
            "kind": "agentd",
            "running": bool(agentd_state.get("running")),
            "pid": agentd_state.get("pid"),
            "status": "running" if agentd_state.get("running") else "stopped",
            "loaded": loaded,
        }

    detected = _detect_providers()
    tools: dict = {}
    agentd_running = bool(backend.get("running")) if selected_backend == "agentd" else False
    for name in ("hermes", "claude", "codex", "openclaw"):
        if selected_backend == "hermes" and name == "hermes":
            continue  # already covered by backend block
        if selected_backend == "agentd" and name in ("claude", "codex"):
            tools[name] = {
                "status": "served via agentd" if agentd_running else "agentd not running",
                "label": detected[name]["label"],
            }
            continue
        tools[name] = {
            "status": detected[name]["status"],
            "label": detected[name]["label"],
        }
    if selected_backend != "hermes":
        readiness = hermes.inspect()
        tools["hermes"]["ready"] = readiness.ready
        tools["hermes"]["url"] = readiness.health_url

    return {
        "selected": {"backend": selected_backend, "tool": user_tool},
        "livekit": livekit,
        "backend": backend,
        "tools": tools,
    }


def _user_facing_tool(cfg: Config) -> str:
    """Resolve the user-facing tool name from the selected provider config.

    ``llm.provider=hermes`` -> ``hermes``.
    ``llm.provider=agentd`` + ``agentd.model=agentd/claude`` -> ``claude``.
    The ``agentd/`` prefix on the model is the canonical way to declare
    which underlying tool the bridge is serving.
    """
    provider = cfg.get("llm.provider", "hermes")
    if provider == "hermes":
        return "hermes"
    if provider == "agentd":
        model = str(cfg.get("agentd.model", "agentd/claude"))
        if "/" in model:
            return model.split("/")[-1]
        return model
    return provider


def _short_tool(model_id: str) -> str:
    """Strip the ``agentd/`` prefix: ``agentd/claude`` -> ``claude``."""
    return model_id.split("/")[-1] if "/" in model_id else model_id


def _format_status_text(payload: dict) -> str:
    """Render the status payload as a human-readable multi-line string."""
    lines: list[str] = []
    sel = payload["selected"]
    if sel["tool"] != sel["backend"]:
        lines.append(f"selected:    {sel['backend']} -> {sel['tool']}")
    else:
        lines.append(f"selected:    {sel['backend']}")
    lk = payload["livekit"]
    lines.append(f"livekit:     {lk['url']} (agent={lk['agent_name']})")

    be = payload["backend"]
    if be.get("kind") == "hermes":
        lines.append(f"backend:     hermes [{be['status']}]   url={be['url']}")
    elif be.get("kind") == "agentd":
        tools_str = ",".join(_short_tool(t) for t in be.get("loaded", [])) or "-"
        pid = be.get("pid") or "-"
        lines.append(
            f"backend:     agentd [{be['status']}]   pid={pid}   loaded=[{tools_str}]"
        )

    if payload["tools"]:
        lines.append("")
        lines.append("other tools:")
        for name in ("hermes", "claude", "codex", "openclaw"):
            info = payload["tools"].get(name)
            if info is None:
                continue
            extra = ""
            if name == "hermes" and "url" in info:
                extra = f"   {info['url']}"
            lines.append(f"  {name:10} {info['status']}{extra}")
    return "\n".join(lines)


# ───────── Command handlers ─────────


def _cmd_init(args, *, out, err) -> int:
    path = _config_path(args.config)
    if path is None:
        from .config import _resolve_default_path

        path = _resolve_default_path()
    try:
        init_config(
            path,
            provider=args.provider,
            interactive=args.provider is None,
            output=lambda msg: print(msg, file=out),
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    return 0


def _cmd_start(args, *, out, err) -> int:
    config_path = _config_path(args.config)
    try:
        cfg = _load_config(args.config)
    except ConfigError:
        if args.provider is None:
            print(
                "error: config not found. Run 'openvox init' first, "
                "or pass '--provider' to auto-configure.",
                file=err,
            )
            return 2
        # --provider given with no config → auto-init.
        if config_path is None:
            config_path = _resolve_default_path()
        # Resolve CLI-style names (claude → agentd, etc.)
        provider = PROVIDER_ALIASES.get(args.provider, args.provider)
        init_config(
            config_path,
            provider=provider,
            interactive=False,
            output=lambda msg: print(msg, file=out),
        )
        cfg = Config.load(config_path)

    try:
        # Extract LiveKit connection settings from config for the worker.
        livekit_env: dict[str, str] = {}
        lk_url = cfg.get("livekit.url")
        if lk_url:
            livekit_env["LIVEKIT_URL"] = lk_url
        lk_key = cfg.get("livekit.api_key")
        if lk_key:
            livekit_env["LIVEKIT_API_KEY"] = lk_key
        lk_secret = cfg.get("livekit.api_secret")
        if lk_secret:
            livekit_env["LIVEKIT_API_SECRET"] = lk_secret
        return orchestrate_start(
            cfg,
            hermes=_build_hermes(cfg),
            agentd=_build_agentd(cfg),
            worker=_WorkerLauncher(
                config_path=config_path,
                livekit_env=livekit_env,
                worker_log_path=_runtime_dir() / "worker.log",
            ),
            auto_start=True,
        )
    except PlannedProviderError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except HermesSetupError as exc:
        print(f"error: {exc}", file=err)
        print(
            "hint: start the gateway with 'hermes gateway start' and configure it "
            "with 'openvox hermes setup --yes'",
            file=err,
        )
        return 1
    except (AgentdSetupError, RuntimeError) as exc:
        print(f"error: {exc}", file=err)
        return 1


def _cmd_stop(args, *, out, err) -> int:
    try:
        cfg = _load_config(args.config)
        orchestrate_stop(hermes=_build_hermes(cfg), agentd=_build_agentd(cfg))
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=err)
        return 1
    print("stopped supervised agentd (if running)", file=out)
    return 0


def _cmd_status(args, *, out, err) -> int:
    try:
        cfg = _load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    payload = collect_status(cfg, hermes=_build_hermes(cfg), agentd=_build_agentd(cfg))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    else:
        print(_format_status_text(payload), file=out)
    return 0


def _cmd_doctor_hermes(args, *, out, err) -> int:
    try:
        cfg = _load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    readiness = _build_hermes(cfg).inspect()
    print(f"status:      {readiness.status}", file=out)
    print(f"cli_path:    {readiness.cli_path}", file=out)
    print(f"cli_version: {readiness.cli_version}", file=out)
    print(f"health_url:  {readiness.health_url}", file=out)
    print(f"detail:      {readiness.detail}", file=out)
    return 0 if readiness.ready else 1


def _probe_url(url: str, *, timeout: float = 3.0) -> bool:
    """Return True if ``url`` responds (any HTTP status, not connection-refused)."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return False


def _run_doctor_checks(cfg: Config) -> list[tuple[str, bool, str]]:
    """Return list of (name, ok, detail) checks for the configured stack."""
    checks: list[tuple[str, bool, str]] = []

    # ── LiveKit connection ──
    lk_url = str(cfg.get("livekit.url", ""))
    if not lk_url:
        checks.append(("LiveKit URL", False, "missing — run 'openvox init' to set"))
    elif not (lk_url.startswith("ws://") or lk_url.startswith("wss://")):
        checks.append(("LiveKit URL", False, f"invalid scheme: {lk_url}"))
    else:
        # Probe the corresponding HTTP(S) endpoint; LiveKit serves an
        # HTTP API on the same host so this catches DNS / port issues.
        from urllib.parse import urlparse
        parsed = urlparse(lk_url)
        scheme = "https" if parsed.scheme == "wss" else "http"
        probe_url = f"{scheme}://{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'wss' else 80)}/"
        reachable = _probe_url(probe_url)
        checks.append((
            "LiveKit URL",
            reachable,
            lk_url if reachable else f"{lk_url} (unreachable)",
        ))

    if cfg.get("livekit.api_key") and cfg.get("livekit.api_secret"):
        checks.append(("LiveKit credentials", True, "configured"))
    else:
        checks.append((
            "LiveKit credentials",
            False,
            "set livekit.api_key + livekit.api_secret in config",
        ))

    # ── Volcengine STT / TTS ──
    for kind in ("stt", "tts"):
        app_id = cfg.get(f"volcengine.{kind}.app_id")
        token = cfg.get(f"volcengine.{kind}.access_token")
        if app_id and token:
            checks.append((f"Volcengine {kind.upper()}", True, "configured"))
        else:
            checks.append((
                f"Volcengine {kind.upper()}",
                False,
                f"set volcengine.{kind}.app_id + access_token",
            ))

    # ── Backend (selected provider) ──
    provider = cfg.get("llm.provider", "hermes")
    if provider == "agentd":
        agentd_rt = _build_agentd(cfg)
        agentd_state = agentd_rt.status()
        if agentd_state.get("running"):
            loaded = agentd_rt.loaded_providers()
            tools = ",".join(_short_tool(t) for t in loaded) or "-"
            checks.append((
                "agentd backend",
                True,
                f"pid={agentd_state.get('pid')}   loaded=[{tools}]",
            ))
        else:
            checks.append((
                "agentd backend",
                False,
                "stopped — run 'openvox start' to bring it up",
            ))
    elif provider == "hermes":
        readiness = _build_hermes(cfg).inspect()
        checks.append((
            "Hermes gateway",
            readiness.ready,
            readiness.detail if readiness.ready else f"{readiness.detail} — start with 'hermes gateway start'",
        ))

    # ── Tool installations ──
    detected = _detect_providers()
    for name in ("hermes", "claude"):
        info = detected[name]
        checks.append((
            f"{name} CLI",
            info["status"] == "installed",
            info["label"],
        ))

    return checks


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_LOG_TIME_RE = re.compile(r'"time"\s*:\s*"([^"]+)"')


def _parse_timespec(spec: str) -> float:
    """Parse '5m' / '1h' / '30s' / '2d' or ISO 8601 into epoch seconds."""
    spec = (spec or "").strip()
    if not spec:
        raise argparse.ArgumentTypeError("time spec must not be empty")
    unit = spec[-1].lower()
    if unit in _DURATION_UNITS and spec[:-1].isdigit():
        return time.time() - int(spec[:-1]) * _DURATION_UNITS[unit]
    candidate = spec.replace("Z", "+00:00") if spec.endswith("Z") else spec
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --since value {spec!r}; use e.g. '5m', '1h', '2d', or ISO 8601"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _parse_tail_count(value: str) -> int:
    """Parse a non-negative log tail count."""
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("tail count must be an integer") from exc
    if count < 0:
        raise argparse.ArgumentTypeError("tail count must be zero or greater")
    return count


def _extract_log_timestamp(line: str) -> float | None:
    """Return epoch seconds for a pino 'time' field, or None when missing."""
    match = _LOG_TIME_RE.search(line)
    if match is None:
        return None
    raw = match.group(1).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _cmd_doctor_all(args, *, out, err) -> int:
    """Run every diagnostic check and print a single report."""
    try:
        cfg = _load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2

    checks = _run_doctor_checks(cfg)
    failed = 0
    for name, ok, detail in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name:24} {detail}", file=out)
        if not ok:
            failed += 1

    print(file=out)
    if failed:
        print(
            f"{failed} check(s) need attention. Run 'openvox init' to fill missing config.",
            file=out,
        )
        return 1
    print("all checks passed.", file=out)
    return 0


def _cmd_doctor(args, *, out, err) -> int:
    """Dispatch on ``args.target``: ``hermes`` -> focused, otherwise all checks."""
    target = getattr(args, "target", "all") or "all"
    if target == "hermes":
        return _cmd_doctor_hermes(args, out=out, err=err)
    return _cmd_doctor_all(args, out=out, err=err)


def _cmd_logs(args, *, out, err) -> int:
    """View / follow runtime logs with ``--tail`` / ``--since`` / ``--grep``."""
    target = args.target
    log_path = _runtime_dir() / f"{target}.log"
    if not log_path.exists():
        print(f"error: log file not found: {log_path}", file=err)
        print(f"hint: run 'openvox start' first to populate {target}.log", file=err)
        return 1
    # --since + --follow are mutually exclusive: a rolling cutoff doesn't make
    # sense when we're streaming new lines into the terminal.
    if args.follow and args.since is not None:
        print("error: --since cannot be combined with --follow", file=err)
        return 2

    # Compile the grep pattern eagerly so we surface a bad regex to the user
    # before shelling out to `tail`.
    pattern = None
    if args.grep:
        try:
            pattern = re.compile(args.grep)
        except re.error as exc:
            print(f"error: invalid --grep pattern: {exc}", file=err)
            return 2

    # Follow mode delegates to `tail -f` (and optionally `grep --line-buffered`)
    # so we get real OS-level follow/buffering semantics. macOS / Linux both
    # ship tail; on Windows we'd need a pure-Python fallback, but the
    # voice-agent runtime is POSIX-first.
    if args.follow:
        cmd = ["tail", "-f", str(log_path)]
        if pattern is None:
            try:
                result = subprocess.run(cmd)
            except FileNotFoundError:
                print("error: 'tail' executable not found in PATH", file=err)
                return 1
            return result.returncode

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            print("error: 'tail' executable not found in PATH", file=err)
            return 1
        return_code = 0
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if pattern.search(line) is not None:
                    out.write(line)
                    out.flush()
            return_code = process.wait()
        except KeyboardInterrupt:
            return_code = 130
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
        return return_code

    if args.since is None and pattern is None and args.tail > 0:
        cmd = ["tail", "-n", str(args.tail), str(log_path)]
        try:
            result = subprocess.run(cmd)
        except FileNotFoundError:
            print("error: 'tail' executable not found in PATH", file=err)
            return 1
        return result.returncode

    # Snapshot mode: read the whole file, apply --since / --grep filters, then
    # honour --tail (0 means "all"). Errors=replace keeps a half-written
    # UTF-8 line from breaking the read on the trailing line.
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        print(f"error: cannot read log file {log_path}: {exc}", file=err)
        return 1

    if args.since is not None:
        cutoff = args.since
        kept: list[str] = []
        for line in lines:
            ts = _extract_log_timestamp(line)
            # Keep lines with no parseable timestamp; they pre-date the
            # logger rollover and the user almost always wants to see them.
            if ts is None or ts >= cutoff:
                kept.append(line)
        lines = kept

    if pattern is not None:
        lines = [line for line in lines if pattern.search(line) is not None]

    if args.tail > 0:
        lines = lines[-args.tail:]

    out.writelines(lines)
    return 0


def _cmd_hermes_setup(args, *, out, err) -> int:
    try:
        cfg = _load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    runtime = _build_hermes(cfg)
    api_key = str(cfg.get("hermes.api_key", ""))
    try:
        result = runtime.setup_api_server(api_key=api_key, apply=args.yes)
    except HermesSetupError as exc:
        print(f"error: {exc}", file=err)
        return 1
    if args.yes:
        print("applied hermes api-server configuration", file=out)
    else:
        print("preview (pass --yes to apply):", file=out)
        for cmd in result or []:
            # Redact the api-key argument so secrets never hit stdout.
            shown = ["***" if tok == api_key and api_key else tok for tok in cmd]
            print("  " + " ".join(shown), file=out)
    return 0


# ───────── Parser ─────────

#: Short help description; the full module docstring is too verbose for ``-h``.
_HELP_DESCRIPTION = "OpenVox voice-agent runtime CLI."

_HELP_EPILOG = """examples:
  openvox init                 pick LLM backend interactively
  openvox start                bring up backend + LiveKit worker
  openvox status               show provider readiness

exit codes:
  0  success
  1  runtime error (backend failed to start)
  2  user error (bad config / unready provider / invalid args)
"""

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openvox",
        description=_HELP_DESCRIPTION,
        epilog=_HELP_EPILOG,
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True, title="commands")

    def _add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default=None, help="path to config.json")

    init_p = sub.add_parser(
        "init", help="write/update config and pick provider",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    _add_config(init_p)
    init_p.add_argument("--provider", choices=list(ALL_PROVIDERS), default=None)
    init_p.set_defaults(handler=_cmd_init)

    start_p = sub.add_parser(
        "start", help="start backend + LiveKit worker",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    _add_config(start_p)
    start_p.add_argument(
        "--provider", "--llm",
        choices=["hermes", "agentd", "claude", "codex", "openclaw"],
        default=None,
        help="LLM backend (auto-detect if omitted)",
    )
    start_p.set_defaults(handler=_cmd_start)

    stop_p = sub.add_parser(
        "stop", help="stop supervised agentd",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    _add_config(stop_p)
    stop_p.set_defaults(handler=_cmd_stop)

    status_p = sub.add_parser(
        "status", help="report provider status",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    _add_config(status_p)
    status_p.add_argument("--json", action="store_true", help="emit JSON")
    status_p.set_defaults(handler=_cmd_status)

    log_p = sub.add_parser(
        "logs", aliases=["log"], help="view or follow runtime logs",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    _add_config(log_p)  # accept (and ignore) --config for consistency
    log_p.add_argument(
        "target",
        nargs="?",
        default="agentd",
        choices=["agentd", "worker"],
        help="which log to show (default: agentd)",
    )
    log_p.add_argument(
        "-n", "--tail", "--lines", dest="tail", type=_parse_tail_count, default=50,
        help="lines to show without --follow; 0 shows all (default: 50)",
    )
    log_p.add_argument(
        "-f", "--follow", action="store_true",
        help="follow log output (like tail -f)",
    )
    log_p.add_argument(
        "--since", type=_parse_timespec, metavar="TIME",
        help="show entries since a duration or ISO 8601 time (for example: 5m)",
    )
    log_p.add_argument(
        "--grep", metavar="REGEX",
        help="show entries matching a regular expression",
    )
    log_p.set_defaults(handler=_cmd_logs)

    doctor_p = sub.add_parser(
        "doctor", help="diagnostics",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    doctor_p.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=["all", "hermes"],
        help="what to check (default: all checks)",
    )
    _add_config(doctor_p)
    doctor_p.set_defaults(handler=_cmd_doctor)

    hermes_p = sub.add_parser(
        "hermes", help="Hermes management",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    hermes_sub = hermes_p.add_subparsers(dest="target", required=True)
    hermes_setup_p = hermes_sub.add_parser(
        "setup", help="configure Hermes api-server",
        formatter_class=RawDescriptionRichHelpFormatter,
    )
    _add_config(hermes_setup_p)
    hermes_setup_p.add_argument("--yes", action="store_true", help="apply changes")
    hermes_setup_p.set_defaults(handler=_cmd_hermes_setup)

    return parser

def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on invalid args; surface that as our return code.
        return int(exc.code) if exc.code is not None else 0
    return args.handler(args, out=sys.stdout, err=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
