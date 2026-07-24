---
type: Runbook
title: OpenVox 本地 Runbook
description: 运维视角的 OpenVox worker 启动、派单、故障排查手册。
tags: [operations, runbook, troubleshooting, livekit, dispatch]
---

# 本地 Runbook

本页是 [架构页](../architecture/overview.md) 的运维侧对位。前提:`~/.openvox/config.json` 已存在(schema 见 [Config loader](../configuration/config-loader.md)),且 `.venv/bin/python` 已就绪。

## 三终端启动

```mermaid
flowchart TD
    A[终端 A:LiveKit server] --> B[终端 B:OpenVox worker]
    B --> C[终端 C:派单 + 客户端]
    A -- docker start voice-assistant-livekit-1 --> A
    B -- ./scripts/start.sh --> B2[后台 python main.py start]
    C -- lk dispatch create --room demo --agent-name openz --> Server[LiveKit 派单给 worker]
    Server --> B
```

### 终端 A — LiveKit server

macOS 开发机上应该已经有 Docker 容器 `voice-assistant-livekit-1` 在跑。如果没有:

```bash
docker run -d --name local-livekit --restart=always \
  -p 7880-7882:7880-7882 -p 7882:7882/udp \
  livekit/livekit-server:latest --dev
```

`--dev` 把 API key/secret 硬编码为 `devkey` / `secret`。如果你的 `~/.openvox/config.json` 不是 `devkey` / `secret` 配对,worker 握手会 401。

### 终端 B — OpenVox worker

```bash
cd <repo-root>
source .venv/bin/activate
./apps/voice-agent/scripts/start.sh            # 后台,日志写到 /tmp/livekit-worker.log
# 或:
./apps/voice-agent/scripts/start.sh fg         # 前台(Ctrl-C 停)
# 或:
./apps/voice-agent/scripts/start.sh status     # 看 pid + 最近 15 行日志
./apps/voice-agent/scripts/start.sh stop       # 杀掉任何占用 8081 的进程
```

`scripts/start.sh` 内部做的事:

1. 探测 `PY`:优先 `.venv/bin/python`,回退 `python3` / `python`。
2. 校验 `$OPENVOX_CONFIG`(默认 `~/.openvox/config.json`)存在且能解析为 JSON。
3. 从 `livekit.*` 段导出 `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`,让 LiveKit SDK 走 `os.environ` 查找时能拿到。
4. `start` 模式先 `lsof -ti:8081 | xargs kill -9`(清理残留 worker IPC 端口),再 `python main.py start > $LOG 2>&1 &` 启动并 sleep 5 秒确认端口起来了。

### 终端 C — 派单 + 客户端

```bash
# 把 agent 派到 demo 房间
lk dispatch create --dev --room demo --agent-name openz

# 给客户端身份生成 join token
lk token create --dev --room demo --identity alice --join

# 选项 1 — 终端零依赖冒烟
lk room join demo --identity alice --dev \
  --publish hello.ogg --auto-subscribe --exit-after-publish

# 选项 2 — 浏览器
ngrok http 7880                                # 暴露 wss URL
# 把 wss URL 粘进 https://meet.livekit.io/custom

# 选项 3 — 本地 React playground
git clone https://github.com/livekit/agents-playground
# 在它 .env 里写 LIVEKIT_URL / API_KEY / SECRET,然后启动
```

派单的 `--agent-name` **必须**等于 config 里的 `livekit.agent_name`(目前 `openz`);详见 [Config loader](../configuration/config-loader.md)。

## 直接命令(跳过脚本)

```bash
# 前台 dev 模式(交互)
python main.py dev

# console 模式(终端 <-> agent 文字对话)
python main.py console

# 冒烟:volcengine 插件能否干净 import
python -c "from livekit.plugins import volcengine; print(volcengine.__all__)"

# 改完插件源码后重新 editable 安装
pip install -e ./apps/voice-agent/plugins/livekit-plugins-volcengine --no-deps
```

## IPC 端口 8081

Worker 给每个派单 job 起一个子进程,用 8081 作为 supervisor ↔ job worker 之间的 IPC 通道。Job 崩溃时端口偶尔会被残留,下一次 `start` 会报:

```
OSError: [Errno 48] address already in use
```

`scripts/start.sh` 自动处理这一步。如果绕开脚本自己跑,记得:

```bash
lsof -ti:8081 | xargs kill -9
```

`docker-compose.yml` 用默认 `bridge` 网络而不是 `host`,目的就是不让 8081 暴露到宿主机(`host` 网络会让多 worker 撞端口)。

## LiveKit 凭证不匹配(401 握手)

`livekit-server --dev` 把 `devkey` / `secret` 硬编码。如果 `~/.openvox/config.json` 用别的配对(比如 `livekit.yaml` 里的 `openz` / `openz-secret`),worker 签的 JWT 会被 server 拒:

```
WSServerHandshakeError 401
```

两条路二选一:

- 起一个裸 `livekit-local` 容器加 `--dev --bind=0.0.0.0`,config 凭证用 `devkey` / `secret`。
- 挂 `livekit.yaml`、去掉 `--dev`,让 `.env` / config 与 server 实际配对一致。仓库的 `apps/voice-agent/CLAUDE.md` 提到 `start-lan.sh` / `start-emu.sh` 用于这两种模式 —— 当前 worktree 里没提交,见 [Quickstart → Backlog](../quickstart.md)。

## 故障排查表

| 症状 | 原因 | 修法 |
|------|------|------|
| 启动 worker 时 `ValueError: api_key is required` | config 里 `volcengine.*.app_id` / `access_token` 缺失 | `cat ~/.openvox/config.json`;补 `volcengine.stt.*` 和 `volcengine.tts.*` |
| `PicklingError: Can't pickle <lambda>` | `prewarm_fnc` 是 lambda | 用模块级函数,签名 `def _prewarm(proc): ...`。`main.py` 默认就是 |
| `prewarm_fnc() takes 0 positional arguments but 1 was given` | `prewarm_fnc` 签名缺 `proc` | 加 `proc` 作为唯一位置参数 |
| 端口 8081 `OSError: address already in use` | 之前的 worker 没清理 | `lsof -ti:8081 \| xargs kill -9` 后再 `./scripts/start.sh` |
| `WSServerHandshakeError 401` | API key/secret 与 LiveKit server 不一致 | 让 `.env` / config 凭证与 server 实际一致(`--dev` 下是 `devkey` / `secret`) |
| `lk dispatch create: agent-name is required` | worker 没设 `agent_name` | config 缺 `livekit.agent_name` |
| `lk token create: failed to fetch` | `LIVEKIT_URL` 不通 / server 没起 | `curl http://localhost:7880/` 应该返回 200;检查容器 |
| 第一次回复时 Hermes api_server 返回 `400 No user message found in messages` | `generate_reply()` 没传 `user_input` | `main.py` 已修(`on_enter` 传 `user_input="打招呼"`) |
| 断开时 worker 日志刷 `exception was never retrieved` | STT `recv_task` 在 `_GatheringFuture` 里抛 `CancelledError` 没人 await | `main.py` 顶部 `_patched_stt_run` 已修 |
| Volcengine STT 返回 403 | AppID 没在控制台开通「流式语音识别 大模型」 | 去 <https://console.volcengine.com/voice/app> 开通 |
| e2e 测试报 `Address already in use` for `livekit_server` | 有别的 test / worker 占着 | 停掉运行中的 worker(`./scripts/start.sh stop`)和任何其他 LiveKit server |

## 测试

`scripts/run_tests.sh` 接 `unit` / `e2e` / `full`(默认 `unit`):

```bash
./apps/voice-agent/scripts/run_tests.sh unit   # tests/ 下全部单元测试,不需要 LiveKit
./apps/voice-agent/scripts/run_tests.sh e2e    # 只跑 tests/e2e_pipeline.py,需要 LiveKit server + worker
./apps/voice-agent/scripts/run_tests.sh full   # 都跑
```

`e2e` 模式需要 `.env`(worker 本身**不读** `.env`,但 `tests/e2e_pipeline.py` 里的 bootstrap 用 `.env` 给 `lk` CLI 调用和 LiveKit Python SDK 设置 `LIVEKIT_*`)。

## Source anchors

- `apps/voice-agent/README.md`(中文操作手册;上面这张表的主要来源)
- `apps/voice-agent/CLAUDE.md` 行 26–69(精炼命令参考 + 坑点)
- `apps/voice-agent/scripts/start.sh` 行 11–53、59–123
- `apps/voice-agent/scripts/run_tests.sh` 行 30–73
- `apps/voice-agent/pyproject.toml` `[tool.pytest.ini_options]`(`pythonpath = ["."]`)
- `apps/voice-agent/.gitignore` 行 8–27(排除 `workspace/users/*`、`workspace/sandbox/*`、`workspace/extensions/mcp/*.local.json` 等运行时目录)