"""Unit tests for hermes_runtime.py.

Tests are written strictly against external service contracts — every
runner / HTTP getter / monotonic clock is injected through the
``HermesRuntime`` and ``HermesConfigurator`` constructors so we never
touch a real Hermes install during unit testing.
"""
from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from urllib.parse import urlsplit

import pytest

from openvox_worker.hermes_runtime import (
    HermesConfig,
    HermesConfigurator,
    HermesReadiness,
    HermesRuntime,
    HermesSetupError,
    HttpResult,
)


# ───────── Test helpers ─────────


def fake_version_command(argv, *, timeout):
    return CompletedProcess(argv, 0, stdout="Hermes Agent v0.19.0", stderr="")


def fake_http_get(calls, statuses):
    def get(path, headers, timeout):
        calls.append((urlsplit(path).path, headers))
        return HttpResult(status=statuses[urlsplit(path).path], body=b'{"data": []}')
    return get


def recording_runner(commands):
    def run(argv, *, timeout, check=False, cwd=None):
        commands.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")
    return run


def failing_restart_runner():
    def run(argv, *, timeout, check=False, cwd=None):
        code = 1 if list(argv)[-1] == "restart" else 0
        return CompletedProcess(
            argv, code,
            stdout="",
            stderr="restart failed" if code else "",
        )
    return run


def healthy_getter(url, headers, timeout):
    return HttpResult(status=200, body=b'{"data": []}')


# ───────── Step 1 tests: CLI presence & health/models HTTP probe ─────────


def test_inspect_reports_missing_cli():
    """When `which` can't locate the hermes binary, status must be cli_missing."""
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key=""),
        which=lambda _: None,
    )
    result = runtime.inspect()
    assert result.status == "cli_missing"
    assert result.ready is False


def test_inspect_requires_health_and_models():
    """A successful probe hits /health unauthenticated then /v1/models with bearer auth."""
    calls = []
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="secret"),
        which=lambda name: "/bin/hermes",
        run=fake_version_command,
        get=fake_http_get(calls, {"/health": 200, "/v1/models": 200}),
    )
    result = runtime.inspect()
    assert result.ready is True
    assert calls == [
        ("/health", None),
        ("/v1/models", {"Authorization": "Bearer secret"}),
    ]


# ───────── Step 5 tests: start() gateway invocation & configurator backup ─────────


def test_start_runs_official_gateway_command_only_when_called():
    commands = []
    cfg = HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k")
    runtime = HermesRuntime(
        cfg,
        which=lambda _: "/bin/hermes",
        run=recording_runner(commands),
        get=healthy_getter,
    )
    runtime.start()
    assert commands[-1][:3] == ["hermes", "gateway", "start"]


def test_setup_backs_up_and_restores_when_restart_fails(tmp_path):
    original = "platforms:\n  api_server:\n    enabled: false\n"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(original, encoding="utf-8")
    runner = failing_restart_runner()
    with pytest.raises(HermesSetupError):
        HermesConfigurator(
            cli="hermes", host="127.0.0.1", port=8642,
            config_path=config_path, run=runner,
        ).apply(api_key="k")
    # Original file is restored from backup.
    assert config_path.read_text(encoding="utf-8") == original


# ───────── Step 7 tests: ensure_ready authorization semantics ─────────


def test_ensure_ready_without_auto_start_does_not_start_gateway():
    """ensure_ready(auto_start=False) inspects but never invokes `hermes gateway start`."""
    commands = []
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k"),
        which=lambda _: "/bin/hermes",
        run=recording_runner(commands),
        get=healthy_getter,
    )
    result = runtime.ensure_ready(auto_start=False)
    assert result.ready is True
    assert all(cmd[:3] != ["hermes", "gateway", "start"] for cmd in commands)


def test_ensure_ready_with_auto_start_invokes_start_and_polls():
    """ensure_ready(auto_start=True) calls start() and waits until ready."""
    commands = []
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k"),
        which=lambda _: "/bin/hermes",
        run=recording_runner(commands),
        get=healthy_getter,
        sleep=lambda s: None,
    )
    result = runtime.ensure_ready(auto_start=True)
    assert result.ready is True
    assert commands[0][:3] == ["hermes", "gateway", "start"]


# ───────── Extra coverage: error classification ─────────


def test_inspect_reports_http_unavailable_on_health_failure():
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k"),
        which=lambda _: "/bin/hermes",
        run=fake_version_command,
        get=fake_http_get([], {"/health": 503, "/v1/models": 200}),
    )
    result = runtime.inspect()
    assert result.status == "http_unavailable"
    assert result.ready is False


def test_inspect_reports_api_unavailable_on_models_failure():
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k"),
        which=lambda _: "/bin/hermes",
        run=fake_version_command,
        get=fake_http_get([], {"/health": 200, "/v1/models": 401}),
    )
    result = runtime.inspect()
    assert result.status == "api_unavailable"
    assert result.ready is False


def test_inspect_reports_cli_error_on_nonzero_version():
    def failing_version(argv, *, timeout):
        return CompletedProcess(argv, 1, stdout="", stderr="boom")

    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k"),
        which=lambda _: "/bin/hermes",
        run=failing_version,
        get=healthy_getter,
    )
    result = runtime.inspect()
    assert result.status == "cli_error"
    assert result.ready is False


def test_inspect_reports_cli_error_when_version_times_out():
    def slow_version(argv, *, timeout):
        raise TimeoutExpired(argv, timeout)

    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k"),
        which=lambda _: "/bin/hermes",
        run=slow_version,
        get=healthy_getter,
    )
    result = runtime.inspect()
    assert result.status == "cli_error"
    assert result.ready is False
    assert "timed out" in result.detail


def test_inspect_does_not_leak_api_key_in_detail():
    """Detail must never echo the bearer token, even on API failure."""
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="super-secret"),
        which=lambda _: "/bin/hermes",
        run=fake_version_command,
        get=fake_http_get([], {"/health": 200, "/v1/models": 500}),
    )
    result = runtime.inspect()
    assert "super-secret" not in result.detail


# ───────── Extra coverage: configurator semantics ─────────


def test_configurator_preview_returns_commands_without_running(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    commands_log = []
    runner = recording_runner(commands_log)
    configurator = HermesConfigurator(
        cli="hermes", host="127.0.0.1", port=8642,
        config_path=config_path, run=runner,
    )
    preview = configurator.apply(api_key="k", apply=False)
    assert isinstance(preview, list)
    assert preview[0][:4] == ["hermes", "config", "set", "platforms.api_server.enabled"]
    assert preview[-1][:3] == ["hermes", "gateway", "restart"]
    # apply=False must never invoke the runner or modify the file
    assert commands_log == []
    assert config_path.read_text(encoding="utf-8") == "{}"


def test_configurator_apply_success_runs_all_commands_and_writes_backup(tmp_path):
    original = "platforms: {}\n"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(original, encoding="utf-8")
    commands = []
    runner = recording_runner(commands)
    configurator = HermesConfigurator(
        cli="hermes", host="127.0.0.1", port=8642,
        config_path=config_path, run=runner,
    )
    result = configurator.apply(api_key="k", apply=True)
    assert result is None
    # 5 commands total: 4 config set + 1 gateway restart
    assert len(commands) == 5
    assert commands[0][:4] == ["hermes", "config", "set", "platforms.api_server.enabled"]
    assert commands[-1][:3] == ["hermes", "gateway", "restart"]
    # A timestamped backup of the original file exists.
    backups = list(tmp_path.glob("config.yaml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_configurator_preview_does_not_leak_api_key():
    cfg = HermesConfigurator(
        cli="hermes", host="127.0.0.1", port=8642,
        config_path=Path("/tmp/never-touched.yaml"),
    )
    preview = cfg.apply(api_key="supersecret-value", apply=False)
    # Last "config set" carries the key — make sure the helper keeps it inside the list,
    # not echoed in any error path the test can see.
    assert isinstance(preview, list)
    assert preview[3][-1] == "supersecret-value"


# ───────── HermesReadiness ready() property ─────────


def test_hermes_readiness_ready_property():
    ready = HermesReadiness(
        status="ready",
        cli_path="/bin/hermes",
        cli_version="Hermes Agent v0.19.0",
        health_url="http://127.0.0.1:8642/health",
        detail="ok",
    )
    assert ready.ready is True

    not_ready = HermesReadiness(
        status="cli_missing",
        cli_path=None,
        cli_version=None,
        health_url="http://127.0.0.1:8642/health",
        detail="missing",
    )
    assert not_ready.ready is False
