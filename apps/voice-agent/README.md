# OpenVox — Volcengine 语音 Agent 本地运行手册

> **OpenVox** 是一个基于 LiveKit Agents 的语音 worker，对接火山引擎（Volcengine）的 STT / TTS，LLM 直连本地 Hermes OpenAI-兼容 api_server。把 `livekit-plugins-volcengine`（来自 [di-osc/livekit-plugins-chinese](https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine)）挂在 LiveKit Server 上。
>
> 默认管线是 **STT + LLM + TTS pipeline**（STT/TTS 走火山引擎，LLM 走 `openai.LLM` → Hermes api_server）。

---

## 0. 先决条件（一次性）

| 工具 | 用途 | 安装 |
|------|------|------|
| Python ≥ 3.10 | 运行 agent | `brew install python@3.11` |
| `livekit-server`（已经在系统里，作为 Docker 容器 `voice-assistant-livekit-1` 在跑） | 本地 SFU/Signaling | `docker ps` 应该已经能看到 |
| `lk`（livekit-cli） | 签 token / dispatch / 客户端 | `brew install livekit-cli` |
| `ffmpeg` | 把 aiff 转成 opus（用 `lk room join --publish` 时需要） | `brew install ffmpeg` |
| `ngrok`（可选） | 把 localhost LiveKit 暴露到公网以便浏览器客户端接入 | `brew install ngrok` |

> 如果 Docker 那个 livekit 容器挂了，重新跑：
> `docker run -d --name local-livekit --restart=always -p 7880-7882:7880-7882 -p 7882:7882/udp livekit/livekit-server:latest --dev`

---

## 1. 初始化项目（首次）

```bash
cd /Users/pz/workspace/openvox

# 1) Python venv
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# 2) 装依赖 + 火山引擎插件（从本地 plugins/ 源码 editable 安装，注意必须传 --no-deps）
pip install "livekit-agents[otel,silero,turn-detector]~=1.5" python-dotenv
pip install -e ./plugins/livekit-plugins-volcengine --no-deps

# 3) 凭证：从 ~/.openvox/config.json 读（schema 见 config.py 模块头注释）
cat ~/.openvox/config.json
#   livekit.url = wss://livekit.openz.top:7443
#   livekit.api_key = openz
#   livekit.agent_name = openvox
#   volcengine.stt.app_id / access_token ...
```

---

## 2. 启动全过程（三终端）

### 终端 A — LiveKit Server

如果 `voice-assistant-livekit-1` 容器已经在跑，跳过；否则手动起：

```bash
docker ps | grep livekit-server       # 应该能看到 healthy
# 或重启：
docker start voice-assistant-livekit-1
```

dev 模式默认密钥是 `devkey` / `secret`，和 `.env` 里一致。

### 终端 B — Volcengine 语音 Agent Worker

```bash
cd /Users/pz/workspace/openvox
source .venv/bin/activate
python main.py start
# 看到 "registered worker" 后即就绪。
```

### 终端 C — 派单 + 客户端

让 agent 去某个房间：

```bash
# 创建一个 dispatch，把 agent 派到 demo 房间
lk dispatch create --dev --room demo --agent-name openz
```

让 alice 进同一个房间说话（三选一）：

| 场景 | 命令 |
|------|------|
| **终端文字/文件测试**（零依赖） | `lk room join demo --identity alice --dev --publish hello.ogg --auto-subscribe --exit-after-publish` |
| **浏览器直接对话** | 先 `ngrok http 7880`，把 wss URL 粘进 <https://meet.livekit.io/custom>，alice 的 token 用 `lk token create --dev --room demo --identity alice --join` 拿 |
| **本地 React playground** | `git clone https://github.com/livekit/agents-playground && 在它 .env 里写 LIVEKIT_URL/LIVEKIT_API_KEY/SECRET` |

---

## 3. 当前管线

当前唯一支持的管线是 `pipeline`（STT + LLM + TTS 三段式），由 `~/.openvox/config.json` 里的 `"pipeline": "pipeline"` 控制。LLM 直连 Hermes gateway 的 OpenAI 兼容 api_server（默认 :8642，配置在 `hermes.api_base`）。

> 注意：STT 段要求 1605412251 这个 AppID 在火山引擎控制台**开通了「流式语音识别 大模型」服务**，否则 STT WebSocket 会 403。

---

## 4. 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `livekit-server --dev` 起不来 → `bind: address already in use` (7882) | Docker livekit 容器占着 7882 | 用 docker 那个，**别自己起** brew server |
| agent 启动时报 `ValueError: api_key is required` | `.env` 没载入 | `source .venv/bin/activate` 后再启动，main.py 顶部有 `load_dotenv()`，检查 `.env` 存在 |
| agent 启动报 `PicklingError: Can't pickle <lambda>` | prewarm_fnc 是 lambda | 已经是 module-level `_prewarm` 函数，若自定义不要用 lambda |
| `prewarm_fnc() takes 0 positional arguments but 1 was given` | prewarm 签名错 | 必须 `def _prewarm(proc): ...`，LiveKit 强制传 proc 参数 |
| agent 启动报 `address already in use` 端口 8081 | 之前的 worker 进程没收掉 | `lsof -ti:8081 \| xargs kill -9` 再重启 |
| `failed to connect to livekit, retrying in 0s ... WSServerHandshakeError 401` | LIVEKIT cred 错了 | 核对 `.env` 里 `LIVEKIT_API_KEY=devkey`、`LIVEKIT_API_SECRET=secret`、`LIVEKIT_URL=ws://localhost:7880` |
| `lk dispatch create` 报 `agent-name is required` | worker 没设 agent_name | main.py 里 `WorkerOptions(agent_name=...)` 必需 |
| `lk token create` 一直打印"failed to fetch" | URL 不对，或 server 没起 | `curl http://localhost:7880/` 看是否 200 |

---

## 5. 项目目录结构

```
openvox/
├── main.py                                 # 入口：Agent + WorkerOptions
├── config.py                               # 启动配置加载（读 ~/.openvox/config.json）
├── pyproject.toml
├── .gitignore
├── CLAUDE.md                               # 给 Claude Code 实例看的架构/坑点摘要
├── README.md                               # 本文件：给人类看的操作手册
├── scripts/
│   ├── start.sh                            # 启动 worker
│   └── run_tests.sh                        # 跑测试
├── plugins/
│   └── livekit-plugins-volcengine/         # vendored 火山引擎插件
├── tests/
└── .venv/                                  # python3.11 venv
```

---

## 6. 关键产物

- `agent worker id` — 终端 B 启动后日志里的 `registered worker ... id="AW_..."`，记下来便于 `lk dispatch list` 查。
- `dispatch id` — `lk dispatch create` 输出的 `id:"AD_..."`，可用 `lk dispatch list` / `lk dispatch delete` 管理。

---

## 7. 下一步（如果你要继续往生产方向推）

1. **STT 开通**：去火山控制台 `https://console.volcengine.com/voice/app` 给 1605412251 勾选「流式语音识别（豆包大模型）」，`PIPELINE=pipeline` 也能用。
2. **LiveKit Cloud / 自托管生产**：把 `.env` 里的 `LIVEKIT_*` 换成真凭证或自托管 server 配置；`main.py` 不需要改。
3. **Function tools**：当前 `VolcengineAgent` 没暴露 `@function_tool`，参考 `examples/voice_agents/basic_agent.py` 加天气查询等。
4. **Dockerize**：写个 `Dockerfile` 把 `vendor/ + main.py + .env` 打包成镜像，部署到 LiveKit Cloud Agents 或自托管 worker 池。
