# OpenVox — 可切换后端的语音 Agent CLI

> **OpenVox** 是一个基于 LiveKit Agents 的语音 worker CLI，对接火山引擎（Volcengine）的 STT / TTS，LLM 后端可选择 **Hermes**（本地 Gateway）或 **agentd**（ACP 桥接 daemon）。`livekit-plugins-volcengine` 来自 [di-osc/livekit-plugins-chinese](https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine)。
>
> 管线始终是 **STT + LLM + TTS pipeline**（语音由火山引擎处理，LLM 通过 OpenAI-兼容端点调用所选后端）。

---

## 先决条件（一次性）

| 工具 | 用途 | 安装 |
|------|------|------|
| Python ≥ 3.10 | 运行 agent | `brew install python@3.11` |
| LiveKit Server（Docker） | 本地 SFU/Signaling | `docker ps` 确认容器在跑 |
| `lk`（livekit-cli） | 签 token / dispatch | `brew install livekit-cli` |
| Hermes CLI（选 Hermes 后端时） | 本地 LLM gateway | `pip install hermes` |
| `node` ≥ 20 + `pnpm`（选 agentd 后端时） | ACP daemon | `brew install node@20` && `npm i -g pnpm` |

---

## 初始化

```bash
cd /Users/pz/workspace/openvox

# 1) Python venv
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# 2) 装 openvox_worker + 火山引擎插件（--no-deps 避免版本冲突）
pip install -e ./apps/voice-agent --no-deps
pip install -e ./apps/voice-agent/plugins/livekit-plugins-volcengine --no-deps

# 3) 配置向导 — 写入 ~/.openvox/config.json，选择后端（hermes / agentd）
openvox init

# 4) 检查配置是否完整
openvox status
```

---

## 启动

统一命令 `openvox` 管理所有后端：

```bash
# 自动拉起所选后端 + LiveKit worker
openvox start --yes

# 查看运行状态
openvox status

# 停止受管的后端进程
openvox stop
```

`openvox start` 会自动执行以下逻辑：

1. 根据 `llm.provider` 选择后端（Hermes 或 agentd）
2. 如果后端未运行，将其拉起
3. 健康检查确认后端就绪
4. 启动 LiveKit worker（`python -m openvox_worker.main start`）
5. 任何一步失败都会回滚已启动的进程

派单测试：

```bash
lk dispatch create --dev --room demo --agent-name openvox
lk room join demo --identity alice --dev --publish hello.ogg --auto-subscribe --exit-after-publish
```

---

## 可用命令

| 命令 | 用途 |
|------|------|
| `openvox init [--provider hermes\|agentd]` | 写入 / 更新 `~/.openvox/config.json`，选择 LLM 后端 |
| `openvox start [--yes]` | 拉起后端 + LiveKit worker |
| `openvox stop` | 停止受管的 agentd 进程 |
| `openvox status [--json]` | 报告各 provider 状态 |
| `openvox doctor hermes` | 诊断 Hermes 就绪性 |
| `openvox hermes setup [--yes]` | 配置 Hermes api_server |

退出码: `0` 成功, `1` 运行时错误（后端启动失败）, `2` 配置 / 参数错误。

---

## 项目结构

```
apps/voice-agent/
├── openvox_worker/                 # Python package (pip installable)
│   ├── __init__.py
│   ├── __main__.py                 # python -m openvox_worker 进入 CLI
│   ├── cli.py                      # openvox init/start/stop/status/doctor 入口
│   ├── main.py                     # LiveKit Agent + WorkerOptions
│   ├── config.py                   # ~/.openvox/config.json 加载器
│   ├── llm_provider.py             # 根据配置选择 LLM 后端
│   ├── process_runtime.py          # 通用进程托管（start/stop/status）
│   ├── hermes_runtime.py           # Hermes gateway 生命周期
│   └── agentd_runtime.py           # agentd 子进程生命周期
├── scripts/
│   ├── start.sh                    # 兼容 shim → 委派给 openvox CLI
│   ├── run_tests.sh                # 测试 runner
│   └── openvox                     # 开发模式启动器
├── plugins/
│   └── livekit-plugins-volcengine/ # vendored 火山引擎插件
├── tests/                          # 23 单元测试 + e2e + runtime 测试
└── pyproject.toml                  # [project.scripts] openvox = ...

apps/agentd/                        # ACP 桥接 daemon（可选，选 agentd 后端时使用）
├── src/                            # TypeScript + Fastify 源码
├── tests/                          # vitest 测试
└── ...
```

---

## 选后端

`~/.openvox/config.json` 的 `llm.provider` 字段控制使用的 LLM 后端：

```jsonc
{
  "llm": { "provider": "hermes" },   // 或 "agentd"
  "hermes": {
    "cli": "hermes",
    "host": "127.0.0.1",
    "port": 8642,
    "api_base": "http://127.0.0.1:8642/v1",
    "api_key": "",
    "model": "hermes-default"
  },
  "agentd": {
    "host": "127.0.0.1",
    "port": 8787,
    "api_base": "http://127.0.0.1:8787/v1",
    "api_key": "",
    "model": "agentd/claude"
  }
}
```

两种后端选择通过 `openvox init --provider hermes|agentd` 一键切换。

### Hermes（默认）
本地 LLM gateway，纯 Python 栈，适合开发调试和轻量使用。`openvox start --yes` 会自动确保 Hermes api_server 就绪。

### agentd
Node.js + Fastify 的 ACP 桥接 daemon，可调用 Claude Code / Codex / OpenClaw 等 ACP 兼容 CLI 作为 LLM 后端。位于 `apps/agentd/`，安装：

```bash
cd apps/agentd
pnpm install && pnpm build
```

---

## 开发模式

未安装 console script 时可用 `python -m openvox_worker` 替代 `openvox`：

```bash
cd /Users/pz/workspace/openvox/apps/voice-agent
python -m openvox_worker status
python -m openvox_worker start --yes
```
