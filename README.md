# LiveKit × Volcengine 语音 Agent — 本地运行手册

> 本目录是一个可在 macOS 上**本地跑通**的 LiveKit Agents worker，把 `livekit-plugins-volcengine`（来自 [di-osc/livekit-plugins-chinese](https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine)）挂在本地 LiveKit Server 上。
>
> 默认管线是 **Realtime E2E**（一条 WebSocket 直连火山引擎 dialogue 端点），也可一键切到 **STT+LLM+TTS pipeline**。

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
cd /Users/pz/workspace/livekit

# 1) Python venv
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# 2) 装依赖 + 火山引擎插件（从本地 vendor/ 源码 editable 安装，注意必须传 --no-deps）
pip install "livekit-agents[otel,silero,turn-detector]~=1.5" python-dotenv
pip install -e ./vendor/volcengine-src/livekit-plugins/livekit-plugins-volcengine --no-deps

# 3) 凭证（已写在 .env 里，不要 commit 真实凭证）
cat .env
#   LIVEKIT_URL=ws://localhost:7880
#   LIVEKIT_API_KEY=devkey
#   LIVEKIT_API_SECRET=secret
#   VOLCENGINE_STT_APP_ID=1605412251  ...等等
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
cd /Users/pz/workspace/livekit
source .venv/bin/activate
python main.py start
# 看到 "registered worker" 后即就绪。
```

### 终端 C — 派单 + 客户端

让 agent 去某个房间：

```bash
# 创建一个 dispatch，把 agent 派到 demo 房间
lk dispatch create --dev --room demo --agent-name volcengine-agent
```

让 alice 进同一个房间说话（三选一）：

| 场景 | 命令 |
|------|------|
| **终端文字/文件测试**（零依赖） | `lk room join demo --identity alice --dev --publish hello.ogg --auto-subscribe --exit-after-publish` |
| **浏览器直接对话** | 先 `ngrok http 7880`，把 wss URL 粘进 <https://meet.livekit.io/custom>，alice 的 token 用 `lk token create --dev --room demo --identity alice --join` 拿 |
| **本地 React playground** | `git clone https://github.com/livekit/agents-playground && 在它 .env 里写 LIVEKIT_URL/LIVEKIT_API_KEY/SECRET` |

---

## 3. 切换管线

**默认：`PIPELINE=realtime`**（Realtime E2E，最少依赖，**不需要 STT**）。

切到 STT + LLM + TTS 三段式：

```bash
PIPELINE=pipeline python main.py start
```

> 注意：这要求 1605412251 这个 AppID 在火山引擎控制台**开通了「流式语音识别 大模型」服务**，否则 STT WebSocket 会 403。Realtime 管线无此限制。

---

## 4. 验证脚本（与 LiveKit 无关，纯发包）

```bash
source .venv/bin/activate
python verify_volcengine.py
```

预期：

```
[1/4] LLM   → POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
  ✓ HTTP 200 reply='你好呀，…' tokens=40
[2/4] TTS   → POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
  ✓ HTTP 200 received N audio chunks, M bytes of mp3 (saved to tts_sample.mp3)
[3/4] RT    → WS  wss://openspeech.bytedance.com/api/v3/realtime/dialogue
  ✓ WS handshake OK — server ack 72 bytes (auth + protocol confirmed)
[4/4] STT   → WS  wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
  ⚠ WS handshake refused (server reachable; service not enabled for this app): 403
```

3/4 端点真打通。第 4 项 STT 403 是火山引擎控制台侧 ASR 服务未开通，不是网络/凭证问题。

---

## 5. 故障排查

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
| Realtime 模型日志里 `Connection reset by peer` 一直重连 | 火山侧鉴权挂了 | 检查 `VOLCENGINE_REALTIME_APP_ID` / `_ACCESS_TOKEN`，确认 plugin 源码里的固定 `X-Api-App-Key: PlgvMymc7f3tQnJ6` 字段没被覆盖 |

---

## 6. 项目目录结构

```
livekit/
├── main.py                                 # 入口：Agent + WorkerOptions
├── verify_volcengine.py                    # 独立连通性测试脚本（4 个端点）
├── .env                                    # 凭证（已 gitignore .local）
├── .gitignore
├── CLAUDE.md                               # 给 Claude Code 实例看的架构/坑点摘要
├── README.md                               # 本文件：给人类看的操作手册
├── vendor/
│   └── volcengine-src/                     # 拉来的 di-osc/livekit-plugins-chinese 源码
│       └── livekit-plugins/livekit-plugins-volcengine/
└── .venv/                                  # python3.11 venv
```

---

## 7. 关键产物

- `tts_sample.mp3` — `verify_volcengine.py` 跑通 TTS 时落盘的 21 KB 真音频（ID3 v2.4 / MPEG ADTS / 24 kHz / mono），证明凭证有效。
- `agent worker id` — 终端 B 启动后日志里的 `registered worker ... id="AW_..."`，记下来便于 `lk dispatch list` 查。
- `dispatch id` — `lk dispatch create` 输出的 `id:"AD_..."`，可用 `lk dispatch list` / `lk dispatch delete` 管理。

---

## 8. 下一步（如果你要继续往生产方向推）

1. **STT 开通**：去火山控制台 `https://console.volcengine.com/voice/app` 给 1605412251 勾选「流式语音识别（豆包大模型）」，`PIPELINE=pipeline` 也能用。
2. **LiveKit Cloud / 自托管生产**：把 `.env` 里的 `LIVEKIT_*` 换成真凭证或自托管 server 配置；`main.py` 不需要改。
3. **Function tools**：当前 `VolcengineAgent` 没暴露 `@function_tool`，参考 `examples/voice_agents/basic_agent.py` 加天气查询等。
4. **Dockerize**：写个 `Dockerfile` 把 `vendor/ + main.py + .env` 打包成镜像，部署到 LiveKit Cloud Agents 或自托管 worker 池。
