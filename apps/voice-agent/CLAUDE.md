# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

**OpenVox** 是一个基于 LiveKit Agents 的语音 worker CLI，对接火山引擎 STT/TTS，LLM 后端可选择 Hermes 或 agentd。

统一入口是 `openvox` 命令（由 `openvox_worker.cli` 提供），用户安装后通过 `openvox init`/`start`/`stop`/`status` 管理整个语音 Agent 实例。LiveKit worker 本身在 `openvox_worker.main`。

## 目录结构

```
apps/voice-agent/
├── openvox_worker/                     # Python package（pip installable）
│   ├── __init__.py / __main__.py
│   ├── cli.py                          # openvox init/start/stop/status/doctor
│   ├── main.py                         # VolcengineAgent + WorkerOptions + 补丁
│   ├── config.py                       # ~/.openvox/config.json 加载器
│   ├── llm_provider.py                 # 按配置选择 LLM 后端端点
│   ├── process_runtime.py              # 通用进程托管（state JSON + SIGTERM）
│   ├── hermes_runtime.py               # Hermes gateway 生命周期（409 行）
│   └── agentd_runtime.py               # agentd 子进程生命周期（347 行）
├── scripts/
│   ├── start.sh                        # 委派给 openvox CLI 的兼容 shim
│   ├── run_tests.sh                    # pytest runner
│   └── openvox                         # 开发模式启动器
├── plugins/livekit-plugins-volcengine/ # vendored 火山引擎 STT/TTS
├── tests/                              # ~46 单元测试 + e2e
└── pyproject.toml                      # [project.scripts] openvox = ...

apps/agentd/                            # ACP 桥接 daemon（TypeScript + Fastify）
├── src/
├── tests/
└── ...
```

## 常用命令

| 操作 | 命令 |
|---|---|
| 激活 venv | `source /path/to/.venv/bin/activate` |
| 配置向导 | `openvox init [--provider hermes\|agentd]` |
| 拉起后端 + worker | `openvox start --yes` |
| 查状态 | `openvox status` |
| 停受管进程 | `openvox stop` |
| 诊断 Hermes | `openvox doctor hermes` |
| 开发模式 | `python -m openvox_worker status` |
| 跑单测 | `./scripts/run_tests.sh unit` |
| 跑 e2e | `./scripts/run_tests.sh e2e` |
| 把 agent 派到房间 | `lk dispatch create --dev --room demo --agent-name openvox` |

`agent_name` 保持 **`openz`**：远端 LiveKit 派单表按此注册，详见 `shared/room-naming.md`。

## 架构要点

- **CLI 入口 `cli.py`** — 466 行，`argparse` 子命令：`init` / `start` / `stop` / `status` / `doctor` / `hermes`。`start` 先拉起所选后端（Hermes readiness 探测 / agentd `pnpm build` + node start），健康检查通过后再起 LiveKit worker。
- **可切换后端** — `llm.provider` 控制（hermes / agentd / codex(planned) / openclaw(planned)）。`llm_provider.build_llm(cfg, openai.LLM)` 构造对应端点的 `openai.LLM` 实例给 `main.py`。
- **进程托管 `process_runtime.py`** — `ProcessSupervisor` + `OwnedProcess`。start 写 `runtime-<name>.json`（0600），stop 先 `is_owned(cmdline fragment)` 验权再 SIGTERM。**不含 SIGKILL、不含端口探测**。
- **Hermes gateway** — `HermesRuntime` 支持 `inspect()` / `start()` / `ensure_ready(auto_start=True)`。用 `hermes --version` + HTTP `/health` + `/v1/models` 三重探测。
- **agentd** — `AgentdRuntime` 管理 Node daemon。`ensure_config` 把 Python Config 投射为 JSON，`start` 检查 `node` + `pnpm install/build` + `node dist/index.js --config <projection>` + 健康检查。失败回滚。
- **main.py 补丁**（5 处，不可删）：
  1. `_FilterNoneChoices` — 过滤 Hermes usage-only chunk，累积 `[LLM-TEXT]` marker
  2. `livekit.cli.log.setup_logging = no-op` — 防 JSON handler 叠加
  3. `volcengine.STT._process_stream_event` wrap — 打 `[用户语音]` 标签
  4. `volcengine.STT._run` wrap — 吞 CancelledError（子进程拆时）
  5. `volcengine.TTS._run` wrap — 吞 CancelledError（同上）

## 已知坑

- **`prewarm_fnc` 必须是模块级函数**，签名 `def _prewarm(proc)`。不能 lambda。
- **editable install + `--no-deps`** — vendored 插件钉 `livekit-agents==1.5.4`，不加 `--no-deps` 把宿主降级拆了 extras。
- **8081 端口** — worker IPC 端口。`scripts/start.sh` 不再自动 `lsof -ti:8081 | xargs kill -9`（v2 改的），端口冲突时手动清。
- **agent_name = `openvox`**（v0.3.0+；v0.2.x 是 `openz`）—— LiveKit 派单表按 `openvox` 注册。**v0.2.x 升级**：老 config `~/.openvox/config.json` 的 `livekit.agent_name` 需手动改为 `openvox`（或重跑 `openvox init`），否则 dispatch 失败。
- **Hermes 要求至少一条 user 消息** — `VolcengineAgent.on_enter` 通过 `generate_reply(user_input="打招呼")` 注入占位。
- **STT AppID 需开通「流式语音识别 大模型」** — 否则 STT WebSocket 403。
- **CLI start 会对 hermes 做 `hermes --version` 探测** — 如果 `hermes` 在本机 PATH 但不可用（挂起），`openvox status` 可能超时。这是 v2 的已知限制。

## 测试

- ~46 单元测试（无外部依赖） + 4 个需要 LiveKit/volcengine 的集成测试
- runtime 模块全部通过依赖注入可测（PopenFactory / PsProbe / KillFn / HttpGetter）
- 跑测试：`cd apps/voice-agent && pytest tests/test_process_runtime.py tests/test_llm_provider.py tests/test_config.py tests/test_hermes_runtime.py tests/test_start_script_contract.py -v`

## 参考资料

- 插件源码：<https://github.com/di-osc/livekit-plugins-chinese>
- LiveKit Agents：<https://github.com/livekit/agents>
- agentd 设计：`TASK_BRIEF.md` in `/Users/pz/workspace/agentd/`
- 上一轮 CLI 设计：`.claude/worktrees/agent-af0af9840fe25fdb0` + `selectable-agent-runtime-v2` 分支
