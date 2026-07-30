"""Unified ``openvox`` runtime CLI.

One entry point to configure and operate the voice-agent stack:

- ``openvox init``          — write / update ``~/.openvox/config.json`` and
                              pick the LLM provider (never echoes secrets).
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
import getpass
import json
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

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

SUPPORTED_PROVIDERS = ("hermes", "agentd")
PLANNED_PROVIDERS = ("codex", "openclaw")
ALL_PROVIDERS = SUPPORTED_PROVIDERS + PLANNED_PROVIDERS

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


def init_config(
    path: Path,
    *,
    provider: str | None,
    input_fn: Callable[[str], str] = input,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    output: Callable[..., None] = print,
    interactive: bool = False,
) -> Config:
    """Write / update ``path`` selecting ``provider``; never echo the secret.

    Existing keys are preserved (deep-safe): only ``llm.provider`` and any
    *missing* keys in the provider's section are filled from
    :data:`PROVIDER_DEFAULTS`. In flag mode (``interactive=False``) no secret
    is read; in interactive mode the API key is collected via ``getpass`` and
    written to the file (mode ``0600``) but never printed.
    """
    data = _read_existing(path)
    if provider is None:
        if interactive and _HAVE_QUESTIONARY:
            choices = [questionary.Choice(title=k, value=k) for k in ALL_PROVIDERS]
            provider = questionary.select("Select LLM provider", choices=choices).ask()
        else:
            entered = input_fn(f"LLM provider {list(ALL_PROVIDERS)} [hermes]: ").strip()
            provider = entered or "hermes"
    if provider not in ALL_PROVIDERS:
        raise ConfigError("unknown llm provider")

    llm = data.setdefault("llm", {})
    llm["provider"] = provider

    section = data.setdefault(provider, {})
    for key, value in PROVIDER_DEFAULTS.get(provider, {}).items():
        section.setdefault(key, value)

    if interactive and provider in SUPPORTED_PROVIDERS:
        if _HAVE_QUESTIONARY:
            secret = questionary.password(f"{provider} API key (blank to skip)").ask() or ""
        else:
            secret = getpass_fn(f"{provider} API key (blank to skip): ").strip()
        if secret:
            section["api_key"] = secret
        secret = None

    _atomic_write_json(path, data)
    output(f"wrote runtime config for provider={provider} -> {path}")
    return Config(data)


def _detect_providers() -> dict:
    """Scan PATH for available LLM providers and return {provider: label}."""
    found = {}
    for provider, binary, label in [
        ("hermes", "hermes", "Hermes (local gateway)"),
        ("claude", "claude", "claude (Claude Code)"),
        ("codex", "codex", "codex (Codex)"),
        ("openclaw", "openclaw", "openclaw (OpenClaw)"),
    ]:
        if shutil.which(binary) is not None:
            found[provider] = label
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

    def __init__(self, *, config_path: Path | None) -> None:
        self._config_path = config_path

    def start(self) -> None:
        env = dict(os.environ)
        if self._config_path is not None:
            env["OPENVOX_CONFIG"] = str(self._config_path)
        result = subprocess.run(
            [sys.executable, "-m", "openvox_worker.main", "start"],
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"livekit worker exited with code {result.returncode}")


# ───────── Orchestration ─────────


def orchestrate_start(
    cfg: Config,
    *,
    hermes: Any,
    agentd: Any,
    worker: Any,
    auto_start: bool = False,
) -> int:
    """Bring up the selected backend then launch the worker.

    Order is fixed: reject planned/unknown providers → Hermes readiness or
    agentd start → worker launch. Any exception reverse-stops the processes
    this call owns (only ``agentd`` is owned; the external Hermes gateway is
    never stopped) before re-raising.
    """
    provider = cfg.get("llm.provider", "hermes")
    if provider in PLANNED_PROVIDERS:
        raise PlannedProviderError(
            f"llm provider {provider} is planned, not yet supported"
        )
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError("unknown llm provider")

    owned: list[Any] = []
    try:
        if provider == "hermes":
            readiness = hermes.ensure_ready(auto_start=auto_start)
            if not readiness.ready:
                raise HermesSetupError(
                    f"hermes gateway not ready: {readiness.status} ({readiness.detail})"
                )
        else:  # agentd
            agentd.start()
            owned.append(agentd)
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
    """Assemble a secret-free status payload for all known providers."""
    readiness = hermes.inspect()
    agentd_state = agentd.status()
    return {
        "selected": cfg.get("llm.provider", "hermes"),
        "providers": {
            "hermes": {"status": readiness.status, "detail": readiness.detail},
            "agentd": {
                "status": "running" if agentd_state.get("running") else "stopped",
                "pid": agentd_state.get("pid"),
            },
            "codex": {"status": "planned"},
            "openclaw": {"status": "planned"},
        },
    }


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
    # Auto-detect providers if no config exists or no provider configured.
    config_path = _config_path(args.config)
    cfg = None
    try:
        cfg = _load_config(args.config)
    except ConfigError:
        pass

    provider = args.provider
    if provider is None and cfg is not None:
        provider = cfg.get("llm.provider")

    if provider is None:
        detected = _detect_providers()
        if not detected:
            print(
                "error: no LLM provider found on PATH. "
                "Install hermes, claude, or codex first.",
                file=err,
            )
            return 2
        available = sorted(detected.keys())
        if len(available) == 1:
            provider = available[0]
            print(f"auto-detected: {detected[provider]}", file=out)
        else:
            if sys.__stdin__.isatty() and _HAVE_QUESTIONARY:
                choices = [questionary.Choice(title=v, value=k) for k, v in detected.items()]
                choices = sorted(choices, key=lambda c: c.value)
                provider = questionary.select("Select LLM provider", choices=choices).ask()
            else:
                print("detected providers:", file=out)
                for i, k in enumerate(available, 1):
                    print(f"  [{i}] {detected[k]}", file=out)
                raw = input(f"select provider [1]: ").strip()
                idx = int(raw) - 1 if raw.isdigit() else 0
                if idx < 0 or idx >= len(available):
                    idx = 0
                provider = available[idx]

        # Auto-init before starting.
        if config_path is None:
            config_path = _resolve_default_path()
        init_config(
            config_path,
            provider=provider,
            interactive=False,
            output=lambda msg: print(msg, file=out),
        )
        cfg = Config.load(config_path)

    try:
        return orchestrate_start(
            cfg,
            hermes=_build_hermes(cfg),
            agentd=_build_agentd(cfg),
            worker=_WorkerLauncher(config_path=config_path),
            auto_start=args.yes,
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
            "with 'openvox hermes setup --yes' (or re-run 'openvox start --yes')",
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
        print(f"selected provider: {payload['selected']}", file=out)
        for name, info in payload["providers"].items():
            print(f"  {name:10} {info['status']}", file=out)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openvox", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default=None, help="path to config.json")

    init_p = sub.add_parser("init", help="write/update config and pick provider")
    _add_config(init_p)
    init_p.add_argument("--provider", choices=list(ALL_PROVIDERS), default=None)
    init_p.set_defaults(handler=_cmd_init)

    start_p = sub.add_parser("start", help="start backend + LiveKit worker")
    _add_config(start_p)
    start_p.add_argument("--yes", action="store_true", help="auto-start backends")
    start_p.add_argument(
        "--provider", "--llm",
        choices=["hermes", "agentd", "claude", "codex", "openclaw"],
        default=None,
        help="LLM backend (auto-detect if omitted)",
    )
    start_p.set_defaults(handler=_cmd_start)

    stop_p = sub.add_parser("stop", help="stop supervised agentd")
    _add_config(stop_p)
    stop_p.set_defaults(handler=_cmd_stop)

    status_p = sub.add_parser("status", help="report provider status")
    _add_config(status_p)
    status_p.add_argument("--json", action="store_true", help="emit JSON")
    status_p.set_defaults(handler=_cmd_status)

    doctor_p = sub.add_parser("doctor", help="diagnostics")
    doctor_sub = doctor_p.add_subparsers(dest="target", required=True)
    doctor_hermes_p = doctor_sub.add_parser("hermes", help="inspect Hermes readiness")
    _add_config(doctor_hermes_p)
    doctor_hermes_p.set_defaults(handler=_cmd_doctor_hermes)

    hermes_p = sub.add_parser("hermes", help="Hermes management")
    hermes_sub = hermes_p.add_subparsers(dest="target", required=True)
    hermes_setup_p = hermes_sub.add_parser("setup", help="configure Hermes api-server")
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
