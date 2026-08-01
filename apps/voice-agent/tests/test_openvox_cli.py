"""Unit tests for the unified ``openvox`` runtime CLI.

Every external side effect (Hermes readiness probe, agentd supervision,
LiveKit worker launch) is injected through the orchestration helpers so
these tests never touch Node, pnpm, Hermes, or the network. The three
``main([...])`` tests exercise the argument parsing + config-writing
surface end to end; the orchestration tests drive ``orchestrate_start`` /
``orchestrate_stop`` with fakes to pin ordering and failure cleanup.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import openvox_worker.cli as openvox_cli
from openvox_worker.agentd_runtime import AgentdSetupError
from openvox_worker.config import Config, ConfigError
from openvox_worker.hermes_runtime import HermesSetupError
from openvox_worker.llm_provider import PlannedProviderError


# ───────── Step 1: required CLI RED tests ─────────


def test_init_writes_selected_provider_without_echoing_secret(tmp_path, capsys):
    path = tmp_path / "config.json"
    assert openvox_cli.main(["init", "--config", str(path), "--provider", "agentd"]) == 0
    data = json.loads(path.read_text())
    assert data["llm"]["provider"] == "agentd"
    assert "api_key" not in capsys.readouterr().out


def test_status_json_contains_planned_catalog(tmp_path, capsys, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "hermes"}}))
    monkeypatch.setattr(openvox_cli, "_build_hermes", lambda cfg: FakeHermes())
    monkeypatch.setattr(openvox_cli, "_build_agentd", lambda cfg: FakeAgentd())
    assert openvox_cli.main(["status", "--config", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    # hermes is the active backend -> other tools still surface planned state.
    assert payload["backend"]["kind"] == "hermes"
    assert payload["tools"]["codex"]["status"] == "planned"
    assert payload["tools"]["openclaw"]["status"] == "planned"


def test_start_rejects_planned_provider(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "codex"}}))
    assert openvox_cli.main(["start", "--config", str(path)]) != 0
    assert "planned" in capsys.readouterr().err


# ───────── Fakes for orchestration ─────────


class FakeHermes:
    def __init__(self, *, ready=True, status="ready"):
        self.calls: list[tuple] = []
        self._ready = ready
        self._status = status

    def ensure_ready(self, *, auto_start=False):
        self.calls.append(("ensure_ready", auto_start))
        return SimpleNamespace(ready=self._ready, status=self._status, detail="d")

    def inspect(self):
        self.calls.append(("inspect", None))
        return SimpleNamespace(
            ready=self._ready,
            status=self._status,
            detail="d",
            cli_path=None,
            cli_version=None,
            health_url="http://h/health",
        )


class FakeAgentd:
    def __init__(self, *, fail_start=False):
        self.events: list[str] = []
        self._fail_start = fail_start

    def start(self):
        self.events.append("start")
        if self._fail_start:
            raise AgentdSetupError("boom")

    def stop(self):
        self.events.append("stop")

    def status(self):
        return {"running": False, "pid": None}

    def loaded_providers(self):
        return []


class FakeWorker:
    def __init__(self, *, fail=False):
        self.events: list[str] = []
        self._fail = fail

    def start(self):
        self.events.append("start")
        if self._fail:
            raise RuntimeError("worker boom")


# ───────── Step 6: orchestration RED tests ─────────


def test_start_agentd_then_worker_in_order():
    cfg = Config({"llm": {"provider": "agentd"}})
    agentd = FakeAgentd()
    worker = FakeWorker()
    rc = openvox_cli.orchestrate_start(
        cfg, hermes=FakeHermes(), agentd=agentd, worker=worker
    )
    assert rc == 0
    assert agentd.events == ["start"]
    assert worker.events == ["start"]


def test_start_worker_failure_reverse_stops_owned():
    cfg = Config({"llm": {"provider": "agentd"}})
    agentd = FakeAgentd()
    worker = FakeWorker(fail=True)
    with pytest.raises(RuntimeError):
        openvox_cli.orchestrate_start(
            cfg, hermes=FakeHermes(), agentd=agentd, worker=worker
        )
    # the owned agentd started in this session must be torn down
    assert agentd.events == ["start", "stop"]


def test_start_hermes_readiness_then_worker():
    cfg = Config({"llm": {"provider": "hermes"}})
    hermes = FakeHermes(ready=True)
    worker = FakeWorker()
    rc = openvox_cli.orchestrate_start(
        cfg, hermes=hermes, agentd=FakeAgentd(), worker=worker
    )
    assert rc == 0
    assert hermes.calls[0][0] == "ensure_ready"
    assert worker.events == ["start"]


def test_start_hermes_not_ready_does_not_launch_worker():
    cfg = Config({"llm": {"provider": "hermes"}})
    hermes = FakeHermes(ready=False, status="cli_missing")
    worker = FakeWorker()
    with pytest.raises(HermesSetupError):
        openvox_cli.orchestrate_start(
            cfg, hermes=hermes, agentd=FakeAgentd(), worker=worker
        )
    assert worker.events == []


def test_stop_stops_agentd_but_not_hermes():
    hermes = FakeHermes()
    agentd = FakeAgentd()
    openvox_cli.orchestrate_stop(hermes=hermes, agentd=agentd)
    assert agentd.events == ["stop"]
    assert hermes.calls == []


def test_start_rejects_planned_before_touching_runtimes():
    cfg = Config({"llm": {"provider": "codex"}})
    agentd = FakeAgentd()
    worker = FakeWorker()
    with pytest.raises(PlannedProviderError):
        openvox_cli.orchestrate_start(
            cfg, hermes=FakeHermes(), agentd=agentd, worker=worker
        )
    assert agentd.events == []
    assert worker.events == []


def test_start_rejects_unknown_provider():
    cfg = Config({"llm": {"provider": "mystery"}})
    with pytest.raises(ConfigError) as exc:
        openvox_cli.orchestrate_start(
            cfg, hermes=FakeHermes(), agentd=FakeAgentd(), worker=FakeWorker()
        )
    assert "mystery" not in str(exc.value)


# ───────── init / status detail ─────────


def test_init_preserves_existing_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"livekit": {"agent_name": "openz"}, "llm": {"provider": "hermes"}})
    )
    openvox_cli.main(["init", "--config", str(path), "--provider", "agentd"])
    data = json.loads(path.read_text())
    assert data["livekit"]["agent_name"] == "openz"
    assert data["llm"]["provider"] == "agentd"
    assert "agentd" in data


def test_status_reports_selected_provider(tmp_path, capsys, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "agentd"}}))
    monkeypatch.setattr(openvox_cli, "_build_hermes", lambda cfg: FakeHermes())
    monkeypatch.setattr(openvox_cli, "_build_agentd", lambda cfg: FakeAgentd())
    assert openvox_cli.main(["status", "--config", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"]["backend"] == "agentd"
    # Default agentd.model = "agentd/claude" -> tool "claude".
    assert payload["selected"]["tool"] == "claude"
    assert "livekit" in payload
    assert payload["backend"]["kind"] == "agentd"
    assert set(payload["tools"]) >= {"hermes", "openclaw"}

# ───────── Provider detection / status badges ─────────


def test_detect_providers_returns_status_dict(monkeypatch):
    """_detect_providers 返回 {provider: {label, status}}；agentd 不暴露。"""
    monkeypatch.setattr(
        openvox_cli.shutil,
        "which",
        lambda name: "/opt/bin/hermes" if name == "hermes" else None,
    )
    detected = openvox_cli._detect_providers()
    assert detected["hermes"] == {
        "label": "Hermes (local gateway)",
        "status": "installed",
    }
    assert detected["claude"] == {
        "label": "Claude Code",
        "status": "not installed",
    }
    assert detected["codex"] == {"label": "Codex", "status": "planned"}
    assert detected["openclaw"] == {"label": "OpenClaw", "status": "planned"}
    assert "agentd" not in detected


def test_init_rejects_not_installed_provider_with_guidance(
    tmp_path, monkeypatch, capsys
):
    """显式选 not-installed provider 时打印安装引导并拒绝写配置。"""
    monkeypatch.setattr(openvox_cli.shutil, "which", lambda _x: None)
    path = tmp_path / "config.json"
    rc = openvox_cli.main(["init", "--config", str(path), "--provider", "hermes"])
    out, err = capsys.readouterr()
    assert rc == 2
    assert "not installed" in err
    assert "pip install hermes" in out
    assert not path.exists()


def test_init_rejects_planned_provider_with_guidance(tmp_path, capsys):
    """显式选 planned provider 时打印计划提示并拒绝写配置。"""
    path = tmp_path / "config.json"
    rc = openvox_cli.main(["init", "--config", str(path), "--provider", "codex"])
    out, err = capsys.readouterr()
    assert rc == 2
    assert "planned" in err
    assert "planned but not yet implemented" in out
    assert not path.exists()


def test_init_interactive_fallback_shows_status_badges(
    tmp_path, monkeypatch, capsys
):
    """无 questionary 时 fallback 菜单显示状态 badge。"""
    monkeypatch.setattr(openvox_cli, "_HAVE_QUESTIONARY", False)
    monkeypatch.setattr(
        openvox_cli.shutil,
        "which",
        lambda name: "/opt/bin/hermes" if name == "hermes" else None,
    )
    inputs = iter(["2", "", "", ""])  # select claude, accept LiveKit defaults (not installed)
    path = tmp_path / "config.json"
    with pytest.raises(ConfigError, match=r"llm provider claude is not installed"):
        openvox_cli.init_config(
            path,
            provider=None,
            interactive=True,
            input_fn=lambda _: next(inputs),
            output=lambda msg: print(msg),
        )
    out, _ = capsys.readouterr()
    assert "✓ Hermes (local gateway) (installed)" in out
    assert "✗ Claude Code (not installed)" in out


def test_init_does_not_prompt_for_api_key_when_agentd_backend(
    tmp_path, monkeypatch
):
    """选择 claude 等 agentd 后端时不应询问 API key（Claude Code 自带认证）。"""
    monkeypatch.setattr(
        openvox_cli.shutil,
        "which",
        lambda name: "/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(openvox_cli, "_HAVE_QUESTIONARY", False)
    # 1 -> provider (claude), 3 -> LiveKit defaults, 4 -> Volcengine defaults.
    inputs = iter(["2", "", "", "", "", "", "", ""])
    path = tmp_path / "config.json"

    openvox_cli.init_config(
        path,
        provider=None,
        interactive=True,
        input_fn=lambda _: next(inputs),
        output=lambda msg: None,
    )
    data = json.loads(path.read_text())
    assert data["llm"]["provider"] == "agentd"
    assert data["agentd"]["api_key"] == ""
    assert data["livekit"]["url"] == "wss://livekit.openz.top"
    # Volcengine STT/TTS sections exist but are empty when defaults accepted.
    assert data["volcengine"]["stt"] == {"app_id": "", "access_token": ""}
    assert data["volcengine"]["tts"] == {"app_id": "", "access_token": ""}


# ───────── doctor ─────────


class _StubAgentdForDoctor:
    def __init__(self, *, running, pid=None, loaded=None):
        self._running = running
        self._pid = pid
        self._loaded = loaded or []

    def status(self):
        return {"running": self._running, "pid": self._pid}

    def loaded_providers(self):
        return list(self._loaded)


def test_doctor_all_reports_missing_volcengine(tmp_path, capsys, monkeypatch):
    """Missing Volcengine creds surfaces as ✗ and the exit code is 1."""
    monkeypatch.setattr(openvox_cli, "_probe_url", lambda *a, **kw: True)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "llm": {"provider": "agentd"},
        "livekit": {
            "url": "wss://livekit.example.com",
            "api_key": "k", "api_secret": "s", "agent_name": "openz",
        },
        "agentd": {
            "host": "127.0.0.1", "port": 8787,
            "api_base": "http://127.0.0.1:8787/v1",
            "api_key": "", "model": "agentd/claude",
        },
        # volcengine intentionally absent.
    }))
    monkeypatch.setattr(
        openvox_cli, "_build_agentd",
        lambda cfg: _StubAgentdForDoctor(running=True, pid=42, loaded=["agentd/claude"]),
    )
    rc = openvox_cli.main(["doctor", "--config", str(path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Volcengine STT" in out
    assert "Volcengine TTS" in out
    assert "✗" in out


def test_doctor_all_passes_when_everything_configured(tmp_path, capsys, monkeypatch):
    """With LiveKit + Volcengine + agentd all good, exit code is 0."""
    monkeypatch.setattr(openvox_cli, "_probe_url", lambda *a, **kw: True)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "llm": {"provider": "agentd"},
        "livekit": {
            "url": "wss://livekit.example.com",
            "api_key": "k", "api_secret": "s", "agent_name": "openz",
        },
        "agentd": {
            "host": "127.0.0.1", "port": 8787,
            "api_base": "http://127.0.0.1:8787/v1",
            "api_key": "", "model": "agentd/claude",
        },
        "volcengine": {
            "stt": {"app_id": "a", "access_token": "t"},
            "tts": {"app_id": "a", "access_token": "t"},
        },
    }))
    monkeypatch.setattr(
        openvox_cli, "_build_agentd",
        lambda cfg: _StubAgentdForDoctor(running=True, pid=42, loaded=["agentd/claude"]),
    )
    rc = openvox_cli.main(["doctor", "--config", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "all checks passed" in out


def test_doctor_hermes_dispatch_keeps_legacy_handler(tmp_path, capsys, monkeypatch):
    """``doctor hermes`` still routes to the focused handler."""
    from openvox_worker.hermes_runtime import HermesReadiness
    monkeypatch.setattr(
        openvox_cli, "_build_hermes",
        lambda cfg: SimpleNamespace(
            inspect=lambda: HermesReadiness(
                status="ready", cli_path="/x",
                cli_version="v1", health_url="http://x/health", detail="ok",
            )
        ),
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "hermes"}}))
    rc = openvox_cli.main(["doctor", "hermes", "--config", str(path)])
    assert rc == 0
    assert "ready" in capsys.readouterr().out


# ───────── log ─────────


def test_help_lists_logs_without_crashing(capsys):
    """Building the parser resolves the logs handler for top-level help."""
    assert openvox_cli.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "logs" in output
    assert "view or follow runtime logs" in output


def test_log_invokes_tail_with_follow_flag(tmp_path, monkeypatch):
    """``openvox log -f`` shells out to ``tail -f <path>``."""
    log_path = tmp_path / "agentd.log"
    log_path.write_text("first line\nsecond line\n")
    monkeypatch.setattr(openvox_cli, "_runtime_dir", lambda: tmp_path)
    captured: dict = {}

    def fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(openvox_cli.subprocess, "run", fake_run)
    rc = openvox_cli.main(["log", "-f", "--config", str(tmp_path / "ignored.json")])
    assert rc == 0
    assert captured["cmd"][0] == "tail"
    assert "-f" in captured["cmd"]
    assert str(log_path) in captured["cmd"]


def test_log_uses_tail_n_when_not_following(tmp_path, monkeypatch):
    """Without ``-f``, ``openvox log -n 100`` passes ``-n 100`` to tail."""
    log_path = tmp_path / "agentd.log"
    log_path.write_text("hello\n")
    monkeypatch.setattr(openvox_cli, "_runtime_dir", lambda: tmp_path)
    captured: dict = {}

    def fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(openvox_cli.subprocess, "run", fake_run)
    rc = openvox_cli.main(
        ["log", "-n", "100", "--config", str(tmp_path / "ignored.json")]
    )
    assert rc == 0
    assert "-n" in captured["cmd"]
    assert "100" in captured["cmd"]
    assert "-f" not in captured["cmd"]


def test_log_errors_when_file_missing(tmp_path, capsys, monkeypatch):
    """When the log file doesn't exist yet, exit non-zero with a hint."""
    monkeypatch.setattr(openvox_cli, "_runtime_dir", lambda: tmp_path)
    rc = openvox_cli.main(["log", "--config", str(tmp_path / "ignored.json")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "log file not found" in captured.err
    assert "openvox start" in captured.err


def test_logs_filters_snapshot_by_time_and_pattern(tmp_path, capsys, monkeypatch):
    """``logs`` applies the new since and grep filters before writing output."""
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        '{"time":"2026-07-29T23:59:59Z","msg":"old ERROR"}\n'
        '{"time":"2026-07-30T00:00:00Z","msg":"current ERROR"}\n'
        '{"time":"2026-07-30T00:00:01Z","msg":"current INFO"}\n'
    )
    monkeypatch.setattr(openvox_cli, "_runtime_dir", lambda: tmp_path)

    rc = openvox_cli.main([
        "logs", "worker",
        "--since", "2026-07-30T00:00:00Z",
        "--grep", "ERROR",
        "--tail", "0",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "current ERROR" in output
    assert "old ERROR" not in output
    assert "current INFO" not in output


# ───────── LLM connectivity probe (used by start summary) ─────────


def test_probe_llm_reports_success_on_2xx(monkeypatch):
    from openvox_worker.cli import _probe_llm_connectivity
    import urllib.request
    settings = SimpleNamespace(
        model="agentd/claude",
        api_base="http://127.0.0.1:8787/v1",
        api_key="",
    )
    class _Resp:
        def __init__(self, status):
            self.status = status
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout: _Resp(200),
    )
    ok, detail = _probe_llm_connectivity(settings)
    assert ok is True
    assert "200" in detail


def test_probe_llm_reports_failure_on_http_error(monkeypatch):
    from openvox_worker.cli import _probe_llm_connectivity
    import urllib.request
    import urllib.error
    settings = SimpleNamespace(
        model="agentd/claude",
        api_base="http://127.0.0.1:8787/v1",
        api_key="",
    )
    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    ok, detail = _probe_llm_connectivity(settings)
    assert ok is False
    assert "401" in detail


def test_probe_llm_reports_failure_on_connection_error(monkeypatch):
    from openvox_worker.cli import _probe_llm_connectivity
    import urllib.request
    import urllib.error
    settings = SimpleNamespace(
        model="agentd/claude",
        api_base="http://127.0.0.1:8787/v1",
        api_key="",
    )
    def boom(req, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    ok, detail = _probe_llm_connectivity(settings)
    assert ok is False
