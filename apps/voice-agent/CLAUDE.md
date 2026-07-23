# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

**OpenVox** 是一个对接 **Volcengine（火山引擎）** 语音服务的 LiveKit Agents worker。`main.py` 注册一个 `livekit-agents` worker，在派单到达时启动一个由火山引擎驱动的 `AgentSession`。当前唯一支持的会话变体是 `pipeline`（STT + LLM + TTS 三段式），由 config 段 `pipeline` 控制。

完整的人工操作手册（三终端启动、浏览器/CLI 客户端、故障排查表）在 `README.md`；本文件聚焦于读代码时容易漏掉的事实。

完整的人工操作手册（三终端启动、浏览器/CLI 客户端、故障排查表）在 `README.md`；本文件聚焦于读代码时容易漏掉的事实。

## 目录结构（关键文件）

- **`main.py`** — worker 入口，`VolcengineAgent`、`_build_session`、`_prewarm`，以及三处日志去重的 monkey-patch。
- **`livekit.yaml`** — 本地 LiveKit server 配置（端口 7880，密钥 `openvox` → secret 哈希）。被 `start-lan.sh` / `start-emu.sh` 挂载进容器；裸的 `livekit-server --dev` 模式使用硬编码的 `devkey/secret` 而忽略此文件。
- **`Dockerfile`** + **`docker-compose.yml`** — 容器化的 worker。`LIVEKIT_URL` 被覆盖为 `ws://host.docker.internal:7880`，让容器能访问宿主机上的 LiveKit server（Docker Desktop / Docker for Mac）。
- **`start.sh` / `start-lan.sh` / `start-emu.sh`** — 本地 LiveKit server 的快捷脚本：
  - `start.sh`：以 `--dev` 模式启动 `livekit-local` 容器并拉起 worker。
  - `start-lan.sh`：用 `en1` 的局域网 IP 作为 `node-ip`，给浏览器/手机客户端用。
  - `start-emu.sh`：用 `10.0.2.2`（Android 模拟器访问宿主机）。
  - 三个脚本都通过 `--node-ip` 传 node IP，LiveKit 1.13.1 的 yaml schema 已经不再接受顶层 `node_ip` 字段。
- **`plugins/livekit-plugins-volcengine/`** — `di-osc/livekit-plugins-chinese` 的 vendored 副本。通过 `pip install -e ... --no-deps` editable 安装（见下方"已知坑"）。`import livekit.plugins.volcengine` 路径保持原样。
- **`tests/e2e_generate_reply.py`** — **已过期**：`sys.path.insert(0, ".../vendor/volcengine-src/...")` 引用了已经不存在的 `vendor/` 路径。运行前需改为 `plugins/livekit-plugins-volcengine`，否则直接删掉。

## 常用命令

| 操作 | 命令 |
|---|---|
| 激活 venv | `source .venv/bin/activate` |
| 对接本地 LiveKit 启动 worker | `./start.sh`（server 已起就 `python main.py start`） |
| dev 派单模式 | `python main.py dev` |
| 控制台模式（终端收发） | `python main.py console` |
| 冒烟测试插件导入 | `python -c "from livekit.plugins import volcengine; print(volcengine.__all__)"` |
| 构建并跑容器化 worker | `docker compose build && docker compose up` |
| 把 agent 派到房间 | `lk dispatch create --dev --room demo --agent-name openvox` |
| 给客户端生成 join token | `lk token create --dev --room demo --identity alice --join` |
| 杀掉卡在 8081 的旧 worker | `lsof -ti:8081 \| xargs kill -9` |

## 插件 API 速查

vendored 插件一律用 keyword-only 参数。**不要**照搬 PyPI 上 `livekit-plugins-volcengine 1.3.0` 的写法（那个版本用 `cluster=` 和旧协议，和 vendored 版一起装会冲突）。

| 组件 | 火山引擎类 | 环境变量前缀 | 构造参数 |
|---|---|---|---|
| STT（流式 ASR） | `volcengine.STT` | `VOLCENGINE_STT_*` | `app_id=...`、`access_token=...`（kw） |
| TTS（豆包 V3 分块 HTTP） | `volcengine.TTS` | `VOLCENGINE_TTS_*` | `app_id=...`（kw）+ `access_token=...`（kw） |
| LLM（豆包 1.5-pro，OpenAI 兼容） | `volcengine.LLM` | `VOLCENGINE_LLM_API_KEY` | `model=...`、`api_key=...`（kw） |

## 架构要点

- **开场白流程** — `VolcengineAgent.on_enter` 调用 `self.session.generate_reply()` 触发 LLM 出一句开场白并经 TTS 合成广播，让客户端进房就能听到招呼声。
- **文本输入** — 客户端通过 DataChannel `TOPIC_CHAT` 发文本。`main.py` 用 `_custom_text_input_cb` 覆盖了默认处理（`sess.interrupt()` + `sess.generate_reply(user_input=ev.text)`），只为加中文日志。`RoomInputOptions(text_input_cb=...)` 必须传给 `session.start(...)` —— **不是** `AgentSession.__init__` 的参数（`livekit-agents 1.5.x` 的契约）。
- **日志去重的三处 patch** — `main.py` 顶部的三段 monkey-patch 是必需而非可选：
  1. `cli.log.setup_logging` 和 `cli._run.setup_logging` 被打成 no-op，避免 LiveKit 的 JSON handler 叠加在我们的 `logging.basicConfig` 上。
  2. `_ProcClient.initialize_logger` 被包了一层：先把从父进程继承下来的 `StreamHandler` 摘掉，再装 IPC 的 `LogQueueHandler`。不这样做的话，每条日志会在子进程 stdout 输出一遍，再通过 IPC 回到主进程输出第二遍，worker 日志完全没法看。

## 已知坑

- **`prewarm_fnc` 必须是模块级函数**，且签名是 `def _prewarm(proc): ...`。`lambda` 在 IPC 跨进程 pickle 时会抛 `PicklingError`。`main.py` 已经用模块级函数，保持这个签名。
- **editable 安装必须加 `--no-deps`**。vendored 插件的 `pyproject.toml` 写死了 `livekit-agents==1.5.4`；不加 `--no-deps` 的话 pip 会把宿主环境的 `livekit-agents` 降到 1.5.4，把 `livekit-agents[otel,silero,turn-detector]~=1.5` 的 extras 全搞坏。`Dockerfile` 和本地 venv 都加了。
- **8081 端口是 worker 的 IPC 端口**。崩溃的 worker 会让端口持续被占，下一次 `start` 报 `OSError: [Errno 48] address already in use`。`start.sh` 会自动 `lsof -ti:8081 | xargs kill -9`；如果绕过脚本启动，这一步要自己来。
- **`lk dispatch` / worker 握手 401 的根因** — `--dev` 模式把密钥硬编码为 `devkey/secret`。如果 `.env` 里 `LIVEKIT_API_KEY/SECRET` 不一致（比如用的是 `livekit.yaml` 里的 `openvox`），worker 签的 JWT server 验不过。两条路二选一：
  - 跑裸的 `livekit-local` 容器加 `--dev --bind=0.0.0.0`，`.env` 写 `devkey/secret`；**或者**
  - 挂载 `livekit.yaml`、去掉 `--dev`（参考 `start-lan.sh` / `start-emu.sh`），让 server 用和 `.env` 一致的 `openz` 密钥。
- **Mac 上的 Docker 网络** — `docker-compose.yml` 把 `LIVEKIT_URL` 覆盖成 `ws://host.docker.internal:7880`，让容器访问 **宿主机** 的 LiveKit server。如果 server 跑在共享网络的兄弟容器里，要换成服务名（`livekit-local:7880`）。compose 故意用默认 `bridge` 网络 —— 切到 `host` 会把 worker 内部的 8081 IPC 端口暴露到宿主机，并行跑多个 worker 就会撞端口。
- **"我们装的是 1.2.9" 是个过时的说法**。vendored 插件的 `pyproject.toml` 钉了 1.5.4，但 `pip install -e ... --no-deps` 会跳过这个 pin，加上 dev/Docker 都用 `~=1.5`，实际装的是 1.5.x。代码里所有针对"我们在 1.2.9"做的分支都要更新；唯一仍然有效的 1.2.9 时代怪癖是 `RoomInputOptions` 走 `session.start()` 而不是 `__init__()`。
- **`~/.openvox/config.json` 里的 `livekit.agent_name`** — 默认是 `volcengine-agent`（livekit-agents 上游约定），但当前配置保持 `openz`（外部 app 还在用 `lk dispatch create --agent-name openz` 派单，改了 worker 收不到单）。等 app 切到新名字后再统一。`lk dispatch create --agent-name …` 必须和 worker 注册名一致，否则派单会一直挂着。
- **测试** — `tests/e2e_generate_reply.py` 是基于旧的 `vendor/` 路径写的，import 就会失败。要么把 `sys.path.insert` 改成指向 `plugins/livekit-plugins-volcengine/livekit`，要么直接删掉；目前仓库里没有其他测试。

## 参考资料

- 插件源码：<https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine>
- 官方 agents 仓库：<https://github.com/livekit/agents>
- 上游基础示例：<https://github.com/livekit/agents/blob/main/examples/voice_agents/basic_agent.py>
- 操作手册（三终端启动、故障排查表、浏览器客户端）：本仓库 `README.md`
- 火山引擎控制台：<https://console.volcengine.com/voice/app>
