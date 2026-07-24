# 可选择 Agent Runtime 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用统一的 OpenVox CLI 管理 Hermes 或 agentd/Claude runtime，并让 LiveKit worker 根据全局配置选择对应的 OpenAI-compatible LLM 服务，同时为 Codex/OpenClaw 留下明确的 planned 占位。

**Architecture:** OpenVox 保留 Python LiveKit worker 和现有 STT/TTS 管线；新增 Python runtime 层负责配置解析、Hermes readiness、Node 子进程和 worker 生命周期。agentd 源码纳入 `apps/agentd`，由 CLI 生成独立配置并启动其 HTTP API；worker 通过一个 LLM factory 选择 Hermes 或 `agentd/claude`。Hermes 始终是外部服务，CLI 只在明确授权时调用其官方配置/启动命令。

**Tech Stack:** Python 3.10+、标准库 `argparse`/`subprocess`/`urllib`/`pathlib`、pytest、LiveKit Agents 1.5、`livekit-plugins-openai==1.6.4`、Node.js 20+、TypeScript、Fastify 5、Vitest、pnpm。

## Global Constraints

- 所有生产行为必须先有一个会失败的测试；先运行并确认失败原因，再写最小实现。
- `llm.provider` 的 active 值只有 `hermes` 和 `agentd`；`codex`、`openclaw` 只能是 `planned`，不得启动 stub。
- 没有 `llm.provider` 的旧配置按 `hermes` 解释。
- Claude 通过 agentd 的 OpenAI-compatible API 接入；不得在 OpenVox 引入 Anthropic SDK 或直接调用 Anthropic API。
- Hermes 先检测 CLI，再检测 `/health`，最后检测带 Bearer 的 `/v1/models`；普通 start 不静默修改 Hermes 配置。
- Hermes 一键配置必须先备份，并通过 Hermes 官方 `config set`/`gateway` 命令完成。
- Node.js 20+ 是 agentd runtime 的必需依赖；不复制 `node_modules`、`dist`、日志或会话状态。
- LiveKit STT、TTS、`AgentSession`、`VolcengineAgent`、DataChannel 和 dispatch 名称保持不变。
- 含密钥配置以原子方式写入并设置 `0600`；日志和错误不得输出完整密钥。
- 默认单测不得启动/停止用户的 Hermes、Claude 或 LiveKit 服务；真实冒烟必须由显式环境变量开启。

## 文件结构与职责

- `apps/agentd/`：从 `/Users/pz/workspace/agentd` 纳入的 TypeScript agentd 源码、配置、测试和文档；不纳入生成产物和依赖目录。
- `apps/agentd/src/config/loader.ts`：支持显式配置路径和 `AGENTD_CONFIG` fallback。
- `apps/agentd/src/cli-args.ts`：纯函数解析 `--check`、`--config`，便于 Vitest 测试。
- `apps/voice-agent/config.py`：保留通用点路径读取，新增 provider 选择和配置写入辅助。
- `apps/voice-agent/llm_provider.py`：把 provider 配置解析成不可变 `LLMSettings`，并构造 OpenAI-compatible LLM。
- `apps/voice-agent/hermes_runtime.py`：Hermes CLI/API readiness、启动提示、官方 CLI 配置、备份恢复。
- `apps/voice-agent/process_runtime.py`：拥有关系明确的子进程启动、健康等待、pid/state 文件和优雅停止。
- `apps/voice-agent/openvox_cli.py`：`init/start/stop/status/doctor/hermes setup` 的 argparse 入口。
- `apps/voice-agent/main.py`：只把现有 LLM 构造切换到 `llm_provider.build_llm`，不改 LiveKit 会话逻辑。
- `apps/voice-agent/tests/`：每个 runtime 单元对应独立 pytest 文件，并保留现有 LiveKit 回归测试。
- `apps/voice-agent/pyproject.toml`：声明 `openvox` console script。
- `apps/voice-agent/scripts/openvox`：未安装包时的仓库内 CLI wrapper。
- `tooling/Taskfile.yaml`、`apps/voice-agent/scripts/start.sh`、`apps/voice-agent/README.md`、`apps/voice-agent/CLAUDE.md`：统一命令和运行说明。

---

### Task 1: 纳入 agentd 并支持受控配置路径

**Files:**
- Create: `apps/agentd/`（复制 `src/`、`tests/`、`docs/`、`package.json`、`pnpm-lock.yaml`、`pnpm-workspace.yaml`、`tsconfig*.json`、`vitest.config.ts`、`scripts/verify.sh`、README）
- Create: `apps/agentd/src/cli-args.ts`
- Modify: `apps/agentd/src/config/loader.ts:7-27,80-83`
- Modify: `apps/agentd/src/index.ts:1-42`
- Test: `apps/agentd/tests/cli-args.test.ts`
- Test: `apps/agentd/tests/config-path.test.ts`

**Interfaces:**
- Produces `parseAgentdArgs(argv: readonly string[]): { check: boolean; configPath?: string }`。
- Produces `startDaemon(configPath?: string): Promise<DaemonHandle>`，未传路径时继续使用 `AGENTD_CONFIG` 或 `~/.agentd/config.json`。

- [ ] **Step 1: 复制不含生成物的 agentd 源树**

```bash
rsync -a --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  --exclude '.claude-session-id' \
  --exclude 'build-stdout.log' \
  --exclude 'SESSION_STATE.md' \
  --exclude 'BUILD_REPORT.md' \
  /Users/pz/workspace/agentd/ apps/agentd/
```

保留源代码、Vitest 测试、pnpm lockfile、API/架构文档和验证脚本；新增 `apps/agentd/.gitignore` 忽略 `node_modules/`、`dist/`、`.agentd/`。

- [ ] **Step 2: 先写解析器失败测试**

```ts
// apps/agentd/tests/cli-args.test.ts
import { describe, expect, it } from 'vitest';
import { parseAgentdArgs } from '../src/cli-args.js';

describe('parseAgentdArgs', () => {
  it('parses --check and an explicit config path', () => {
    expect(parseAgentdArgs(['--check', '--config', '/tmp/openvox-agentd.json'])).toEqual({
      check: true,
      configPath: '/tmp/openvox-agentd.json',
    });
  });

  it('rejects --config without a value', () => {
    expect(() => parseAgentdArgs(['--config'])).toThrow(/--config requires a path/);
  });
});
```

- [ ] **Step 3: 运行 RED 测试**

运行：`pnpm --dir apps/agentd test -- tests/cli-args.test.ts`

预期：FAIL，原因是 `../src/cli-args.js` 不存在。

- [ ] **Step 4: 写最小解析器和配置路径实现**

`src/cli-args.ts` 只处理 `--check`、`--config <path>` 和未知参数错误；`loader.ts` 增加：

```ts
export function resolveConfigPath(explicit?: string): string {
  return explicit ?? process.env.AGENTD_CONFIG ?? getConfigPath();
}

export async function loadConfig(configPath?: string): Promise<AgentdConfig> {
  const resolvedPath = resolveConfigPath(configPath);
  // 保留原有读取、默认合并和 schema 校验逻辑，后续所有路径使用 resolvedPath。
}
```

`daemon.ts` 将 `startDaemon(configPath?: string)` 的参数传给 `loadConfig(configPath)`；`index.ts` 使用 `parseAgentdArgs(process.argv.slice(2))`，不再手工扫描 argv。

- [ ] **Step 5: 写配置路径失败测试并验证**

```ts
// apps/agentd/tests/config-path.test.ts
import { describe, expect, it } from 'vitest';
import { resolveConfigPath } from '../src/config/loader.js';

describe('resolveConfigPath', () => {
  it('prefers explicit path over AGENTD_CONFIG', () => {
    process.env.AGENTD_CONFIG = '/tmp/from-env.json';
    expect(resolveConfigPath('/tmp/explicit.json')).toBe('/tmp/explicit.json');
  });
});
```

先运行并确认旧实现无法满足显式路径优先，再运行该测试与完整 agentd 测试。

- [ ] **Step 6: 运行 GREEN 和 TypeScript 检查**

运行：

```bash
pnpm --dir apps/agentd test
pnpm --dir apps/agentd typecheck
```

预期：全部 PASS、TypeScript 无错误。

- [ ] **Step 7: 提交独立变更**

```bash
git add apps/agentd
git commit -m "feat: import agentd runtime with configurable path"
```

---

### Task 2: 建立 Python provider 配置与 LLM factory

**Files:**
- Create: `apps/voice-agent/llm_provider.py`
- Modify: `apps/voice-agent/config.py:0-106`
- Modify: `apps/voice-agent/main.py:281-301`
- Modify: `apps/voice-agent/tests/test_main_build_session.py:20-68`
- Create: `apps/voice-agent/tests/test_llm_provider.py`
- Test: `apps/voice-agent/tests/test_config.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    api_base: str
    api_key: str

def resolve_llm_settings(cfg: Config) -> LLMSettings:
    raise NotImplementedError

def build_llm(cfg: Config, llm_constructor: Callable[..., Any]) -> Any:
    raise NotImplementedError
```

`resolve_llm_settings` 对缺失 selector 使用 `hermes`；对 `codex`/`openclaw` 抛 `PlannedProviderError(ConfigError)`；对未知值抛 `ConfigError`。

- [ ] **Step 1: 写 provider factory RED 测试**

```python
# apps/voice-agent/tests/test_llm_provider.py
from unittest.mock import Mock
import pytest
from config import Config, ConfigError
from llm_provider import PlannedProviderError, build_llm, resolve_llm_settings

def test_missing_selector_keeps_hermes_compatibility():
    cfg = Config({"hermes": {"model": "h", "api_base": "http://h/v1", "api_key": "hk"}})
    assert resolve_llm_settings(cfg).provider == "hermes"

def test_agentd_maps_its_own_endpoint():
    cfg = Config({"llm": {"provider": "agentd"}, "agentd": {
        "model": "agentd/claude", "api_base": "http://127.0.0.1:8787/v1", "api_key": "ak"
    }})
    constructor = Mock(return_value="llm")
    assert build_llm(cfg, constructor) == "llm"
    constructor.assert_called_once_with(model="agentd/claude", base_url="http://127.0.0.1:8787/v1", api_key="ak")

def test_planned_provider_is_rejected():
    cfg = Config({"llm": {"provider": "codex"}})
    with pytest.raises(PlannedProviderError, match="planned"):
        resolve_llm_settings(cfg)
```

- [ ] **Step 2: 运行 RED**

运行：`cd apps/voice-agent && pytest tests/test_llm_provider.py -q`

预期：FAIL，原因是 `llm_provider.py` 和 `PlannedProviderError` 不存在。

- [ ] **Step 3: 写最小实现**

在 `llm_provider.py` 中按上面的接口读取 `cfg.get("llm.provider", "hermes")`，对 active provider 调用 `cfg.require` 读取三项，统一调用传入的 constructor。`ConfigError` 的错误信息只包含 provider/key 名，不包含值。

- [ ] **Step 4: 运行 GREEN**

运行：`cd apps/voice-agent && pytest tests/test_llm_provider.py tests/test_config.py -q`

预期：PASS。

- [ ] **Step 5: 将 main 接入 factory 并先扩展失败测试**

在 `test_main_build_session.py` 增加 agentd fake config 测试，断言 `openai.LLM` 得到 agentd 三元组；把现有 Hermes 测试明确设置 `"llm": {"provider": "hermes"}`，再运行测试确认改动前版本不能满足新断言。

- [ ] **Step 6: 最小修改 main**

将 `_build_session` 中的 `openai.LLM(...)` 替换为：

```python
from llm_provider import build_llm

llm=build_llm(_cfg, openai.LLM),
```

不移动 STT/TTS、`AgentSession` 参数或 monkey-patch。

- [ ] **Step 7: 回归并提交**

运行：

```bash
cd apps/voice-agent
pytest tests/test_llm_provider.py tests/test_config.py tests/test_main_build_session.py -q
```

然后提交：`git add apps/voice-agent && git commit -m "feat: select LLM provider from config"`。

---

### Task 3: 实现 Hermes CLI/API readiness 与一键配置

**Files:**
- Create: `apps/voice-agent/hermes_runtime.py`
- Create: `apps/voice-agent/tests/test_hermes_runtime.py`
- Modify: `apps/voice-agent/config.py`（配置写入辅助）

**Interfaces:**

```python
@dataclass(frozen=True)
class HermesConfig:
    cli: str
    api_base: str
    api_key: str
    host: str = "127.0.0.1"
    port: int = 8642
    startup_timeout_seconds: float = 20.0

@dataclass(frozen=True)
class HermesReadiness:
    status: Literal["ready", "cli_missing", "cli_error", "http_unavailable", "api_unavailable"]
    cli_path: str | None
    cli_version: str | None
    health_url: str
    detail: str
    @property
    def ready(self) -> bool:
        raise NotImplementedError

class HermesConfigurator:
    def apply(self, *, api_key: str, apply: bool = True) -> None:
        raise NotImplementedError

class HermesRuntime:
    def inspect(self) -> HermesReadiness:
        raise NotImplementedError
    def start(self) -> None:
        raise NotImplementedError
    def wait_until_ready(self) -> HermesReadiness:
        raise NotImplementedError
    def setup_api_server(self, *, api_key: str, apply: bool) -> None:
        raise NotImplementedError
```

构造函数接收可注入的 `which`、`run`、`get`、`sleep`、`clock`，默认绑定 `shutil.which`、`subprocess.run`、`urllib.request.urlopen`，让单测不碰本机服务。

- [ ] **Step 1: 写 CLI 缺失与健康成功测试**

```python
from subprocess import CompletedProcess
from urllib.parse import urlsplit
from hermes_runtime import HermesConfig, HermesRuntime, HttpResult

def fake_version_command(argv, *, timeout):
    return CompletedProcess(argv, 0, stdout="Hermes Agent v0.19.0", stderr="")

def fake_http_get(calls, statuses):
    def get(path, headers, timeout):
        calls.append((urlsplit(path).path, headers))
        return HttpResult(status=statuses[urlsplit(path).path], body=b'{"data": []}')
    return get

def test_inspect_reports_missing_cli():
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key=""),
        which=lambda _: None,
    )
    result = runtime.inspect()
    assert result.status == "cli_missing"
    assert result.ready is False

def test_inspect_requires_health_and_models():
    calls = []
    runtime = HermesRuntime(
        HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="secret"),
        which=lambda name: "/bin/hermes",
        run=fake_version_command,
        get=fake_http_get(calls, {"/health": 200, "/v1/models": 200}),
    )
    result = runtime.inspect()
    assert result.ready is True
    assert calls == [("/health", None), ("/v1/models", {"Authorization": "Bearer secret"})]
```

- [ ] **Step 2: 运行 RED**

运行：`cd apps/voice-agent && pytest tests/test_hermes_runtime.py -q`

预期：FAIL，因为 runtime 模块和状态对象尚不存在。

- [ ] **Step 3: 写最小 readiness 实现**

`inspect()` 按 CLI → `GET <api_base.rstrip('/').removesuffix('/v1')>/health` → `GET <api_base.rstrip('/')>/models` 顺序执行；捕获 `FileNotFoundError`、命令非零、HTTP/URL 错误，转换成稳定状态，不把 token 放进 `detail`。`wait_until_ready()` 使用单调时钟和固定 0.25 秒轮询，超过 `startup_timeout_seconds` 返回最后状态。

- [ ] **Step 4: 运行 GREEN**

运行：`cd apps/voice-agent && pytest tests/test_hermes_runtime.py::test_inspect_reports_missing_cli tests/test_hermes_runtime.py::test_inspect_requires_health_and_models -q`，预期 PASS。

- [ ] **Step 5: 写自动启动和配置备份失败测试**

```python
def recording_runner(commands):
    def run(argv, *, timeout, check=False, cwd=None):
        commands.append(list(argv))
        return CompletedProcess(argv, 0, stdout="", stderr="")
    return run

def failing_restart_runner():
    def run(argv, *, timeout, check=False, cwd=None):
        code = 1 if list(argv)[-1] == "restart" else 0
        return CompletedProcess(argv, code, stdout="", stderr="restart failed" if code else "")
    return run

def healthy_getter(url, headers, timeout):
    return HttpResult(status=200, body=b'{"data": []}')

def test_start_runs_official_gateway_command_only_when_called():
    commands = []
    cfg = HermesConfig(cli="hermes", api_base="http://127.0.0.1:8642/v1", api_key="k")
    runtime = HermesRuntime(cfg, which=lambda _: "/bin/hermes", run=recording_runner(commands), get=healthy_getter)
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
    assert config_path.read_text(encoding="utf-8") == original
```

- [ ] **Step 6: 运行 RED 并实现最小 setup**

运行上述两个测试确认失败；实现 `HermesConfigurator.apply`：复制带时间后缀的 backup、依次调用：

```text
hermes config set platforms.api_server.enabled true
hermes config set platforms.api_server.extra.host <host>
hermes config set platforms.api_server.extra.port <port>
hermes config set platforms.api_server.extra.key <api_key>
hermes gateway restart
```

任一步非零都用备份恢复并抛出 `HermesSetupError`；默认 `apply=False` 只返回命令预览，不写文件。

- [ ] **Step 7: 测试交互/非交互授权语义**

增加测试：没有 `auto_start` 时 `ensure_ready` 不调用 runner；`auto_start=True` 时调用 start 并轮询；CLI `--yes` 才传 `auto_start=True`。运行完整 Hermes 测试并确认 PASS。

- [ ] **Step 8: 提交**

```bash
git add apps/voice-agent/hermes_runtime.py apps/voice-agent/tests/test_hermes_runtime.py apps/voice-agent/config.py
git commit -m "feat: add Hermes CLI and HTTP readiness"
```

---

### Task 4: 实现 agentd 与 LiveKit worker 的受管进程生命周期

**Files:**
- Create: `apps/voice-agent/process_runtime.py`
- Create: `apps/voice-agent/agentd_runtime.py`
- Create: `apps/voice-agent/tests/test_process_runtime.py`
- Create: `apps/voice-agent/tests/test_agentd_runtime.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class OwnedProcess:
    name: str
    pid: int
    command: tuple[str, ...]
    log_path: Path

class ProcessSupervisor:
    def start(self, name: str, command: Sequence[str], *, cwd: Path, log_path: Path) -> OwnedProcess:
        raise NotImplementedError
    def stop(self, process: OwnedProcess) -> None:
        raise NotImplementedError
    def is_owned(self, pid: int, expected_fragment: str) -> bool:
        raise NotImplementedError

class AgentdRuntime:
    def ensure_config(self) -> Path:
        raise NotImplementedError
    def start(self) -> OwnedProcess:
        raise NotImplementedError
    def stop(self) -> None:
        raise NotImplementedError
    def status(self) -> dict[str, object]:
        raise NotImplementedError
```

- [ ] **Step 1: 写 supervisor RED 测试**

```python
class FakePopen:
    def __init__(self, *, pid: int):
        self.pid = pid

    def __call__(self, command, **kwargs):
        return self

class FakeSupervisor:
    def start(self, name, command, *, cwd, log_path):
        return OwnedProcess(name, 4321, tuple(command), log_path)

    def stop(self, process):
        return None

def test_start_records_pid_and_command(tmp_path):
    popen = FakePopen(pid=4321)
    supervisor = ProcessSupervisor(popen_factory=popen)
    owned = supervisor.start("agentd", ["node", "dist/index.js"], cwd=tmp_path, log_path=tmp_path / "agentd.log")
    assert owned.pid == 4321
    assert owned.command == ("node", "dist/index.js")
    assert (tmp_path / "runtime-agentd.json").exists()
```

- [ ] **Step 2: 运行 RED**

运行：`cd apps/voice-agent && pytest tests/test_process_runtime.py -q`；预期因模块不存在失败。

- [ ] **Step 3: 实现 supervisor**

使用 `subprocess.Popen(command, cwd=cwd, start_new_session=True, stdout=log, stderr=subprocess.STDOUT)`；状态 JSON 保存 pid、command、log path 和 `owned: true`。停止前用注入的 `ps` 检查命令包含预期 fragment，验证失败时只删除 stale state，不发送 kill，避免 PID 复用误杀。

- [ ] **Step 4: 写 agentd 配置投影与启动 RED 测试**

```python
def test_agentd_projection_uses_openvox_settings(tmp_path):
    cfg = Config({"agentd": {
        "host": "127.0.0.1", "port": 8787, "api_key": "token",
        "model": "agentd/claude", "api_base": "http://127.0.0.1:8787/v1"
    }})
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
```

- [ ] **Step 5: 实现 agentd runtime**

`ensure_config()` 原子写 `~/.openvox/runtime/agentd.json`；`start()` 检查 Node、`apps/agentd/dist/index.js`，必要时执行 `pnpm install --frozen-lockfile` 和 `pnpm build`（命令可通过依赖注入测试），再启动 `node dist/index.js --config <projection>`，轮询 `/health` 和 `/v1/models`。健康失败必须停止刚启动的 owned process。

- [ ] **Step 6: 运行 GREEN**

运行：

```bash
cd apps/voice-agent
pytest tests/test_process_runtime.py tests/test_agentd_runtime.py -q
```

预期 PASS，并补充异常退出、重复 start、stop 不杀非 owned PID 的断言。

- [ ] **Step 7: 提交**

```bash
git add apps/voice-agent/process_runtime.py apps/voice-agent/agentd_runtime.py apps/voice-agent/tests
git commit -m "feat: supervise agentd runtime process"
```

---

### Task 5: 实现统一 `openvox` CLI 和配置向导

**Files:**
- Create: `apps/voice-agent/openvox_cli.py`
- Create: `apps/voice-agent/tests/test_openvox_cli.py`
- Create: `apps/voice-agent/scripts/openvox`
- Modify: `apps/voice-agent/pyproject.toml:1-18`

**Interfaces:**

```python

def build_parser() -> argparse.ArgumentParser:
    raise NotImplementedError

def init_config(path: Path, *, provider: str | None, input_fn=input, output=print) -> Config:
    raise NotImplementedError

def main(argv: Sequence[str] | None = None) -> int:
    raise NotImplementedError
```

命令：`init`、`start`、`stop`、`status`、`doctor hermes`、`hermes setup`。`status --json` 输出不含密钥的 JSON；planned provider 显示 `planned`。

- [ ] **Step 1: 写 CLI RED 测试**

```python
def test_init_writes_selected_provider_without_echoing_secret(tmp_path, capsys):
    path = tmp_path / "config.json"
    assert main(["init", "--config", str(path), "--provider", "agentd"]) == 0
    data = json.loads(path.read_text())
    assert data["llm"]["provider"] == "agentd"
    assert "api_key" not in capsys.readouterr().out

def test_status_json_contains_planned_catalog(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "hermes"}}))
    assert main(["status", "--config", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"]["codex"]["status"] == "planned"
    assert payload["providers"]["openclaw"]["status"] == "planned"

def test_start_rejects_planned_provider(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"provider": "codex"}}))
    assert main(["start", "--config", str(path)]) != 0
    assert "planned" in capsys.readouterr().err
```

- [ ] **Step 2: 运行 RED**

运行：`cd apps/voice-agent && pytest tests/test_openvox_cli.py -q`；预期因 `openvox_cli.py` 不存在失败。

- [ ] **Step 3: 实现 parser、init 和 status**

`init` 读取已有 JSON（不存在则写最小可编辑模板），只更新 `llm.provider` 与对应默认段；flags 模式不读取 secret，交互模式使用 `getpass.getpass`。写入复用配置原子写函数。`status` 调用对应 runtime 的只读检查，输出 `available/degraded/unavailable/planned`。

- [ ] **Step 4: 运行 GREEN**

运行三个 CLI 测试，预期 PASS；再运行 `pytest tests/test_config.py tests/test_llm_provider.py tests/test_openvox_cli.py -q`。

- [ ] **Step 5: 实现 start/stop/doctor/setup 编排**

`start` 顺序固定为：加载配置 → 拒绝 planned → Hermes readiness 或 agentd start → 启动 worker → 失败时反向清理本次 owned 进程。Hermes readiness 失败时交互输出 `hermes gateway start` 和 `hermes config set` 命令；`--yes` 才执行。`doctor hermes` 只调用 `inspect()`。`hermes setup` 调用 Task 3 的 configurator。

- [ ] **Step 6: 写编排测试并验证 RED→GREEN**

用 fake Hermes/Agentd/Worker runtime 注入，断言顺序、失败清理和 `stop` 不调用 Hermes stop；先运行新断言确认失败，再实现最小编排，最后运行完整 CLI 测试。

- [ ] **Step 7: 接入 console script 和仓库 wrapper**

在 `pyproject.toml` 增加：

```toml
[project.scripts]
openvox = "openvox_cli:main"
```

`scripts/openvox` 使用仓库 `.venv/bin/python` 优先级，执行 `openvox_cli.py`，并设置可执行权限。运行 `python -m pip install -e apps/voice-agent` 后验证 `openvox --help`。

- [ ] **Step 8: 提交**

```bash
git add apps/voice-agent/openvox_cli.py apps/voice-agent/tests/test_openvox_cli.py apps/voice-agent/scripts/openvox apps/voice-agent/pyproject.toml
git commit -m "feat: add unified openvox runtime CLI"
```

---

### Task 6: 接回现有启动脚本、Taskfile 与文档

**Files:**
- Modify: `apps/voice-agent/scripts/start.sh:1-124`
- Modify: `tooling/Taskfile.yaml:26-46,49-52`
- Modify: `apps/voice-agent/README.md`
- Modify: `apps/voice-agent/CLAUDE.md`
- Create: `apps/voice-agent/tests/test_start_script_contract.py`

**Interfaces:**
- 旧 `scripts/start.sh` 保留 `start/fg/stop/status` 兼容参数，但内部委托 `openvox_cli.py`，不再按端口无条件 `kill -9`。
- Taskfile 的 `dev:agent` 调用 `python openvox_cli.py start`，新增 `dev:init`、`dev:status`。

- [ ] **Step 1: 写脚本契约 RED 测试**

```python
def test_start_script_delegates_to_openvox_cli():
    text = Path("scripts/start.sh").read_text()
    assert "openvox_cli.py" in text
    assert "kill -9" not in text
```

- [ ] **Step 2: 运行 RED**

运行：`cd apps/voice-agent && pytest tests/test_start_script_contract.py -q`；预期旧脚本因包含端口 `kill -9` 失败。

- [ ] **Step 3: 最小改脚本和 Taskfile**

保留环境变量导出和 `fg` 兼容，但把生命周期交给 CLI；Taskfile 目标只调用统一入口，不重复实现 provider 逻辑。

- [ ] **Step 4: 更新文档并运行 GREEN**

文档明确：

- `openvox init --provider hermes|agentd`；
- Hermes CLI/API 检查、一键 `openvox hermes setup --yes` 的风险和备份行为；
- Claude 需要 Node 20+、Claude CLI 登录和 agentd；
- Codex/OpenClaw 当前为 planned，不可启动；
- `openvox start/stop/status` 的进程边界。

运行脚本契约、全部 Python 单测和 `task --list`，预期 PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/voice-agent/scripts/start.sh tooling/Taskfile.yaml apps/voice-agent/README.md apps/voice-agent/CLAUDE.md apps/voice-agent/tests/test_start_script_contract.py
git commit -m "docs: document selectable runtime commands"
```

---

### Task 7: LiveKit 回归、agentd 构建和端到端验证

**Files:**
- Modify: `apps/voice-agent/tests/test_main_build_session.py`
- Modify: `apps/voice-agent/tests/test_openai_llm_hermes_compat.py`
- Create: `apps/voice-agent/tests/test_agentd_smoke.py`
- Modify: `apps/voice-agent/README.md`（验证命令）

- [ ] **Step 1: 写 agentd smoke 测试（默认跳过）**

```python
@pytest.mark.skipif(os.getenv("OPENVOX_AGENTD_E2E") != "1", reason="explicit agentd smoke opt-in")
def test_agentd_claude_models_endpoint():
    response = urllib.request.urlopen("http://127.0.0.1:8787/v1/models", timeout=5)
    payload = json.load(response)
    assert any(item["id"] == "agentd/claude" for item in payload["data"])
```

- [ ] **Step 2: 运行默认回归**

```bash
cd apps/voice-agent
pytest -q
```

预期所有默认测试 PASS，agentd/Hermes 真实 smoke 显示 skipped 而非失败。

- [ ] **Step 3: 安装并构建 agentd**

```bash
pnpm --dir apps/agentd install --frozen-lockfile
pnpm --dir apps/agentd build
pnpm --dir apps/agentd typecheck
pnpm --dir apps/agentd test
```

预期构建产物只出现在 worktree 的 `apps/agentd/dist`，由 `.gitignore` 忽略。

- [ ] **Step 4: 显式执行 Hermes 只读诊断**

在不修改服务的前提下运行：

```bash
OPENVOX_CONFIG="$HOME/.openvox/config.json" \
  python apps/voice-agent/openvox_cli.py doctor hermes
```

记录 CLI、`/health`、`/v1/models` 的真实状态；若未就绪，只验证输出引导，不执行 `setup` 或 `gateway start`。

- [ ] **Step 5: 显式执行 agentd/Claude smoke（环境具备时）**

```bash
OPENVOX_AGENTD_E2E=1 python apps/voice-agent/openvox_cli.py start --yes
OPENVOX_AGENTD_E2E=1 pytest apps/voice-agent/tests/test_agentd_smoke.py -q
python apps/voice-agent/openvox_cli.py status --json
python apps/voice-agent/openvox_cli.py stop
```

若本机缺 Claude 凭据或 LiveKit 凭据，必须记录清晰失败原因，不把失败伪装成通过。

- [ ] **Step 6: 最终检查与提交**

```bash
git diff --check
git status --short
git log --oneline -10
```

确认没有密钥、`node_modules`、`dist` 或运行时 pid/log 被追踪；运行完整 Python/TypeScript 测试后提交最终修正：

```bash
git add .
git commit -m "test: verify selectable agent runtime end to end"
```

## 最终验收标准

- 旧 Hermes 配置（无 `llm.provider`）仍能构造原有 `openai.LLM`。
- `llm.provider=agentd` 时 worker 使用 `agentd.api_base/api_key/model`，并由 CLI 自动启动 agentd。
- Hermes 缺 CLI、HTTP 未启动、API 未启用和鉴权失败都有不同可行动提示；一键 setup 有备份与失败恢复测试。
- `openvox init/start/stop/status/doctor/hermes setup` 有稳定退出码和测试覆盖。
- Codex/OpenClaw 显示 `planned`，写入 active 配置会被拒绝，不启动 stub。
- 所有默认测试通过；真实外部服务测试只有显式 opt-in 才执行。
- LiveKit STT/TTS、AgentSession、开场白和文本输入回归测试继续通过。
