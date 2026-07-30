"""Unit tests for agentd_runtime.py.

The AgentdRuntime supervises a local ``agentd`` Node process. All side effects
— ``which`` lookups, subprocess runs (``pnpm``), the ProcessSupervisor, and
HTTP health polling — are injected so tests never touch Node, pnpm, or the
network.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from openvox_worker.agentd_runtime import AgentdRuntime
from openvox_worker.config import Config
from openvox_worker.hermes_runtime import HttpResult
from openvox_worker.process_runtime import OwnedProcess


# ───────── Fakes ─────────


class FakeSupervisor:
    """Records start/stop; returns a deterministic OwnedProcess."""

    def __init__(self, *, owned_pid: int = 4321):
        self._pid = owned_pid
        self.started: list[tuple] = []
        self.stopped: list[OwnedProcess] = []
        self.owned_answer = True

    def start(self, name, command, *, cwd, log_path):
        owned = OwnedProcess(name, self._pid, tuple(command), log_path)
        self.started.append((name, tuple(command), cwd, log_path))
        return owned

    def stop(self, process):
        self.stopped.append(process)

    def is_owned(self, pid, expected_fragment):
        return self.owned_answer


def recording_runner():
    commands: list[list[str]] = []

    def run(argv, *, cwd=None, timeout=None, check=False):
        commands.append(list(argv))
        # Simulate `pnpm build` producing the dist artifact so ensure_built
        # succeeds in tests that don't seed dist/ themselves.
        if list(argv)[-1] == "build" and cwd:
            dist = Path(cwd) / "dist" / "index.js"
            dist.parent.mkdir(parents=True, exist_ok=True)
            dist.write_text("// built\n")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    return run, commands


def healthy_get(url, headers, timeout):
    return HttpResult(status=200, body=b'{"data": []}')


def make_config(**overrides):
    agentd = {
        "host": "127.0.0.1",
        "port": 8787,
        "api_key": "token",
        "model": "agentd/claude",
        "api_base": "http://127.0.0.1:8787/v1",
    }
    agentd.update(overrides)
    return Config({"agentd": agentd})


# ───────── Step 4: config projection ─────────


def test_agentd_projection_uses_openvox_settings(tmp_path):
    cfg = make_config()
    runtime = AgentdRuntime(
        cfg=cfg,
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=FakeSupervisor(),
        http_get=lambda *args, **kwargs: HttpResult(status=200, body=b'{"data": []}'),
    )
    path = runtime.ensure_config()
    data = json.loads(path.read_text())
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 8787
    assert data["auth"]["tokens"] == ["token"]


def test_projection_is_written_atomically_0600(tmp_path):
    cfg = make_config()
    runtime = AgentdRuntime(
        cfg=cfg,
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=FakeSupervisor(),
        http_get=healthy_get,
    )
    path = runtime.ensure_config()
    assert path.name == "agentd.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ───────── Step 5: start lifecycle ─────────


def _dist(repo_root):
    dist = repo_root / "apps" / "agentd" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.js").write_text("// built\n")


def test_start_uses_dist_index_with_config_flag(tmp_path):
    _dist(tmp_path)
    run, commands = recording_runner()
    supervisor = FakeSupervisor()
    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=supervisor,
        http_get=healthy_get,
        which=lambda name: "/usr/bin/node",
        run=run,
        sleep=lambda s: None,
    )
    owned = runtime.start()
    assert owned.pid == 4321
    (name, command, cwd, log_path) = supervisor.started[-1]
    assert command[0] == "node"
    assert command[1].endswith("apps/agentd/dist/index.js")
    assert command[2] == "--config"
    assert command[3].endswith("agentd.json")
    # dist present → no pnpm build/install invoked
    assert commands == []


def test_start_builds_when_dist_missing(tmp_path):
    run, commands = recording_runner()
    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=FakeSupervisor(),
        http_get=healthy_get,
        which=lambda name: "/usr/bin/node",
        run=run,
        sleep=lambda s: None,
    )
    runtime.start()
    joined = [" ".join(c) for c in commands]
    assert any("install" in j and "--frozen-lockfile" in j for j in joined)
    assert any("build" in j for j in joined)
    assert all("apps/agentd" in j for j in joined)


def test_start_raises_when_node_missing(tmp_path):
    _dist(tmp_path)
    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=FakeSupervisor(),
        http_get=healthy_get,
        which=lambda name: None,
        run=recording_runner()[0],
        sleep=lambda s: None,
    )
    with pytest.raises(RuntimeError):
        runtime.start()


def test_health_failure_stops_owned_process(tmp_path):
    _dist(tmp_path)
    supervisor = FakeSupervisor()

    def failing_get(url, headers, timeout):
        return HttpResult(status=503, body=b"")

    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=supervisor,
        http_get=failing_get,
        which=lambda name: "/usr/bin/node",
        run=recording_runner()[0],
        sleep=lambda s: None,
        poll_attempts=2,
    )
    with pytest.raises(RuntimeError):
        runtime.start()
    # the just-started owned process must be stopped on health failure
    assert len(supervisor.stopped) == 1
    assert supervisor.stopped[0].pid == 4321


def test_start_polls_health_then_models(tmp_path):
    _dist(tmp_path)
    calls: list[tuple[str, object]] = []

    def recording_get(url, headers, timeout):
        calls.append((url, headers))
        return HttpResult(status=200, body=b'{"data": []}')

    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=FakeSupervisor(),
        http_get=recording_get,
        which=lambda name: "/usr/bin/node",
        run=recording_runner()[0],
        sleep=lambda s: None,
    )
    runtime.start()
    paths = [u for (u, _h) in calls]
    assert any(u.endswith("/health") for u in paths)
    assert any(u.endswith("/v1/models") for u in paths)
    # models probe carries bearer auth, health does not
    health = next((h) for (u, h) in calls if u.endswith("/health"))
    models = next((h) for (u, h) in calls if u.endswith("/v1/models"))
    assert health is None
    assert models == {"Authorization": "Bearer token"}


# ───────── stop delegates to supervisor via persisted state ─────────


def test_stop_reconstructs_owned_from_state_and_delegates(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    state = {
        "name": "agentd",
        "pid": 4321,
        "command": ["node", "/repo/apps/agentd/dist/index.js", "--config", "c"],
        "log_path": str(runtime_dir / "agentd.log"),
        "owned": True,
    }
    (runtime_dir / "runtime-agentd.json").write_text(json.dumps(state))
    supervisor = FakeSupervisor()
    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=runtime_dir,
        supervisor=supervisor,
        http_get=healthy_get,
    )
    runtime.stop()
    assert len(supervisor.stopped) == 1
    assert supervisor.stopped[0].pid == 4321
    assert supervisor.stopped[0].command == (
        "node",
        "/repo/apps/agentd/dist/index.js",
        "--config",
        "c",
    )


def test_stop_without_state_is_noop(tmp_path):
    supervisor = FakeSupervisor()
    runtime = AgentdRuntime(
        cfg=make_config(),
        repo_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
        supervisor=supervisor,
        http_get=healthy_get,
    )
    runtime.stop()
    assert supervisor.stopped == []
