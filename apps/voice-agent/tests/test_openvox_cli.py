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


def test_status_json_contains_planned_catalog(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "hermes"}}))
    assert openvox_cli.main(["status", "--config", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"]["codex"]["status"] == "planned"
    assert payload["providers"]["openclaw"]["status"] == "planned"


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


def test_status_reports_selected_provider(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "agentd"}}))
    assert openvox_cli.main(["status", "--config", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"] == "agentd"
    assert set(payload["providers"]) >= {"hermes", "agentd", "codex", "openclaw"}

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
    inputs = iter(["2"])  # select Claude Code (not installed)
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
    inputs = iter(["2"])  # select Claude Code
    path = tmp_path / "config.json"

    def _fail_getpass(_prompt):
        raise AssertionError("should not prompt for API key")

    openvox_cli.init_config(
        path,
        provider=None,
        interactive=True,
        input_fn=lambda _: next(inputs),
        getpass_fn=_fail_getpass,
        output=lambda msg: None,
    )
    data = json.loads(path.read_text())
    assert data["llm"]["provider"] == "agentd"
    assert data["agentd"]["api_key"] == ""
