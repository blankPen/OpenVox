# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

**OpenVox** 是一个对接 **Volcengine（火山引擎）** 语音服务的 LiveKit Agents worker。`main.py` 注册一个 `livekit-agents` worker，在派单到达时启动一个由火山引擎驱动的 `AgentSession`。当前唯一支持的会话变体是 STT → LLM → TTS pipeline（`main._build_session()` 直接拼装，没有切换开关；realtime / qwen-realtime 变体已在重构中移除，AST 静态扫描测试锁住）。

完整的人工操作手册（三终端启动、浏览器/CLI 客户端、故障排查表）在 `README.md`；本文件聚焦于读代码时容易漏掉的事实。

## 目录结构（关键文件）

- **`main.py`** — worker 入口；`VolcengineAgent`、`_build_session`、`_prewarm`、`entrypoint`，以及五处日志/异常的 monkey-patch。
- **`config.py`** — `~/.openvox/config.json` 加载器；点路径 `require` / `get`；单例缓存。**单一**配置源，worker 不再读 `.env`。
- **`pyproject.toml`** — 依赖面：`livekit-agents[otel,silero,turn-detector]~=1.5`、`livekit-plugins-openai==1.6.4`，加 vendored 火山引擎插件（本地路径）。
- **`scripts/start.sh`** — `start` / `fg` / `stop` / `status` 四模式；启动前先 `lsof -ti:8081 | xargs kill -9` 清残留 worker IPC 端口。
- **`scripts/run_tests.sh`** — `unit` / `e2e` / `full` 三档 pytest。e2e 模式要求 worker 已经在跑并写日志到 `E2E_WORKER_LOG`。
- **`plugins/livekit-plugins-volcengine/`** — `di-osc/livekit-plugins-chinese` 的 vendored 副本，只保留 STT / TTS 两个类（LLM / RealtimeModel 已移除，OpenVox 不用）。通过 `pip install -e ./apps/voice-agent/plugins/livekit-plugins-volcengine --no-deps` editable 安装。
- **`tests/`** — `test_config.py`（config 单元测试）、`test_main_build_session.py`（pipeline 拼装 + AST 锁）、`test_volcengine_agent.py`（agent 人设 + AST 锁）、`test_openai_llm_hermes_compat.py`（`_FilterNoneChoices` 流过滤器）、`e2e_pipeline.py`（多轮真实音频回放 e2e）。

## 常用命令

| 操作 | 命令 |
|---|---|
| 激活 venv | `source .venv/bin/activate`（venv 在仓库根 `.venv/`，由 `python3.11 -m venv .venv` 建） |
| 起 worker（后台，日志到 `/tmp/livekit-worker.log`） | `./scripts/start.sh` |
| 起 worker（前台） | `./scripts/start.sh fg` |
| 查 worker 状态 | `./scripts/start.sh status` |
| 停 worker | `./scripts/start.sh stop` |
| dev 派单模式 | `python main.py dev` |
| 控制台模式（终端 <-> agent 文字对话） | `python main.py console` |
| 冒烟：volcengine 插件能否干净 import | `python -c "from livekit.plugins import volcengine; print(volcengine.__all__)"` |
| 跑单测 | `./scripts/run_tests.sh unit` |
| 跑 e2e（需要 LiveKit server + worker） | `./scripts/run_tests.sh e2e` |
| 跑全部 | `./scripts/run_tests.sh full` |
| 把 agent 派到房间 | `lk dispatch create --dev --room demo --agent-name openz` |
| 给客户端生成 join token | `lk token create --dev --room demo --identity alice --join` |
| 杀掉卡在 8081 的旧 worker | `lsof -ti:8081 \| xargs kill -9` |

> `agent_name` 保持 `openz`：远端 LiveKit 派单表仍按 `openz` 注册，外部 app 也还在派 `openz`。改 worker 名会导致派单失败 —— 见 `shared/room-naming.md` 的"历史"注。

## 插件 API 速查

vendored 插件一律用 keyword-only 参数。**不要**照搬 PyPI 上 `livekit-plugins-volcengine 1.3.0` 的写法（那个版本用 `cluster=` 和旧协议，和 vendored 版一起装会冲突）。

| 组件 | 火山引擎类 | 构造参数 |
|---|---|---|
| STT（流式 ASR） | `volcengine.STT` | `app_id=...`、`access_token=...`（kw） |
| TTS（豆包 V3 分块 HTTP） | `volcengine.TTS` | `app_id=...`（kw）+ `access_token=...`（kw） |

LLM 不在 vendored 插件里 —— OpenVox 用 `livekit-plugins-openai` 的 `openai.LLM` 指向本地 Hermes api_server（`hermes.{api_base,api_key,model}`），与 STT/TTS 的火山引擎凭证完全解耦。

## 架构要点

- **开场白流程** — `VolcengineAgent.on_enter` 调用 `self.session.generate_reply(user_input="打招呼")` 触发 LLM 出一句开场白并经 TTS 合成广播，让客户端进房就能听到招呼声。**必须**显式传 `user_input=...`，否则 `livekit-agents 1.6.x` 的 `_pipeline_reply_task_impl` 不会往 `chat_ctx` 插入 user 消息，Hermes 网关严格校验 OpenAI 兼容 chat 接口，会回 `400 No user message found in messages` 把 LLM 调用炸掉。
- **文本输入** — 客户端通过 DataChannel `TOPIC_CHAT` 发文本。`main.py` 用 `_custom_text_input_cb` 覆盖默认处理（`sess.interrupt()` + `sess.generate_reply(user_input=ev.text)`），只为加中文日志；语义保持原样。`RoomInputOptions(text_input_cb=...)` 必须传给 `session.start(...)` —— **不是** `AgentSession.__init__` 的参数（`livekit-agents 1.5.x` / `1.6.x` 的契约）。
- **模块加载时的五处 patch**（`main.py` 顶部，缺一不可）：
  1. `openai.resources.chat.completions.AsyncCompletions.create` 被 `_safe_create` 包成流过滤器 `_FilterNoneChoices`：丢弃 `chunk.choices is None` 的 usage-only 块（Hermes 网关在 `stream_options.include_usage=True` 时会发这种），同时累积 `delta.content` 到流结束 / `aclose` 时通过 `_logger.info("[LLM-TEXT] ...")` 把整段回复文本打印出来 —— e2e 测试用这个 marker 抓 agent 真实回复文本。
  2. `livekit.agents.cli.log.setup_logging` 被改成 no-op，避免 LiveKit CLI 的 JSON handler 叠加在我们的 `logging.basicConfig(force=True)` 上每条日志打两遍。
  3. `livekit.plugins.volcengine.stt.SpeechStream._process_stream_event` 被 wrap：原方法跑完后，patch 解析 payload 并在 `utterances[0].definite` 为 `True` 时打 `[用户语音] <text>` —— 给控制台一个"用户到底说了什么"的可视锚点；`logging.basicConfig` 不会自动展开 `extra={"text": ...}`。
  4. `volcengine.SpeechStream._run` 被 wrap：吞掉 `asyncio.CancelledError`（保留原 finally 里的 `ws.close()` + `gracefully_cancel()`），否则 `livekit-agents 1.6.x` 子进程拆掉时 `_GatheringFuture` 异常会以 `exception was never retrieved` 形式污染 stderr。
  5. `volcengine.tts.SynthesizeStream._run` 也对称地吞 `CancelledError`，原因同 4 —— HTTP 同步迭代器上没有 inner `recv_task` 可 drain，patch 在 `_run` 入口 swallow。

## 已知坑

- **`prewarm_fnc` 必须是模块级函数**，且签名是 `def _prewarm(proc): ...`。`lambda` 在 IPC 跨进程 pickle 时会抛 `PicklingError`。`main.py` 已经用模块级函数，保持这个签名。
- **editable 安装必须加 `--no-deps`**。vendored 插件的 `pyproject.toml` 写死了 `livekit-agents==1.5.4`；不加 `--no-deps` 的话 pip 会把宿主环境的 `livekit-agents` 降到 1.5.4，把 `livekit-agents[otel,silero,turn-detector]~=1.5` 的 extras 全搞坏。`scripts/start.sh` 不动 deps，只用 `pip install -e ... --no-deps` 那一行装一次。
- **8081 端口是 worker 的 IPC 端口**。崩溃的 worker 会让端口持续被占，下一次 `start` 报 `OSError: [Errno 48] address already in use`。`scripts/start.sh` 会自动 `lsof -ti:8081 | xargs kill -9`；如果绕过脚本启动，这一步要自己来。
- **`lk dispatch` / worker 握手 401 的根因** — LiveKit `--dev` 模式把 API key/secret 硬编码为 `devkey` / `secret`，worker 的 `livekit-agents` SDK 从 `os.environ["LIVEKIT_URL"]` / `["LIVEKIT_API_KEY"]` / `["LIVEKIT_API_SECRET"]` 读（由 `scripts/start.sh` 从 config 导出）。如果远端 server 不是 `--dev` 模式而是挂了 `livekit.yaml`，凭证就得改成 server 实际配置的 key/secret。`livekit.api_key` 改完后必须 `restart worker`（不是热加载）。
- **Mac 上的 Docker 网络** — 顶层 `infra/docker-compose.yml` 用默认 `bridge` 网络，目的就是不让 worker 内部的 8081 IPC 端口暴露到宿主机。LiveKit 容器监听 7880/7881/7882，宿主 worker 用 `ws://localhost:7880` 连。如果以后把 worker 也 Docker 化并部署到生产，再考虑把 `infra/` 改成 `livekit.yaml` + `node-ip` 显式配置（backlog 项）。
- **livekit-agents 版本** — 仓库根 `pyproject.toml` 钉的是 `~=1.5`（实际 1.5.x），vendored 火山引擎插件里写的是 `==1.5.4`，但 `pip install -e ... --no-deps` 跳过 deps 检查，宿主 venv 维持 1.5.x。**唯一仍然要按 1.5.x 契约写的代码**是 `RoomInputOptions` 必须传给 `session.start(...)` 而不是 `AgentSession.__init__()`；旧 1.2.9 时代怪癖。
- **`~/.openvox/config.json` 里的 `livekit.agent_name`** — 保持 `openz`，远端派单表按这个注册。改 worker 名会让 LiveKit 不再把 job 派过来；`lk dispatch create --agent-name …` 必须和这个值完全一致。
- **Hermes api_server 要求 `chat.messages` 至少一条 user 消息** —— `VolcengineAgent.on_enter` 通过 `generate_reply(user_input="打招呼")` 触发占位 user 消息注入 `chat_ctx` 来满足这条约束。**不要**改成裸 `generate_reply()`。
- **Volcengine STT AppID 必须在控制台开通「流式语音识别 大模型」**，否则 STT WebSocket 返回 403。

## 参考资料

- 插件源码（上游）：<https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine>
- 官方 agents 仓库：<https://github.com/livekit/agents>
- 上游基础示例：<https://github.com/livekit/agents/blob/main/examples/voice_agents/basic_agent.py>
- 操作手册（三终端启动、故障排查表、浏览器客户端）：本仓库 `README.md`
- 火山引擎控制台：<https://console.volcengine.com/voice/app>

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
