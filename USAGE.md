# USAGE — OpenVox

> 装好之后怎么把 OpenVox 跑起来。覆盖 5 分钟 walk-through、命令矩阵、配置速查、典型工作流、故障排查。

如果你还没装，看 [INSTALLATION.md](./INSTALLATION.md)；想理解整个系统怎么搭的，看 [ARCHITECTURE.md](./ARCHITECTURE.md)；运行时细节 / 配置字段 / 已知坑看 [openwiki/](./openwiki/)。

---

## 1. 5 分钟 walk-through

```bash
# (1) 拉代码 + 装环境（首次；二次跳过）
git clone <repo-url> openvox && cd openvox
./scripts/install.sh

# (2) 初始化配置（首次；写 ~/.openvox/config.json）
openvox init
# 交互会问：
#   LLM 后端选哪个？(hermes / agentd / codex / openclaw)
#   如果选 hermes → 是否现在 setup？
#   如果选 agentd → 是否现在 build + 启动？

# (3) 起 backend + LiveKit worker
openvox start --yes
# 看到 "registered worker" 即就绪（worker 跑在前台，Ctrl-C 停）

# (4) 派单 + 拿 token（另开终端）
lk dispatch create --dev --room demo --agent-name openvox
lk token create --dev --room demo --identity alice --join

# (5) 起 Flutter 客户端（再开终端；iOS / Android / macOS / Web 任选）
cd apps/voice-client
flutter run

# (6) 说话 —— 客户端 UI 里按住麦克风 → 看到 agent 回应 → 完成首次端到端

# (7) 停掉
#   Ctrl-C 退出 worker
openvox stop                # 停受管的 backend
(cd ../infra && docker compose down)   # 停 LiveKit
```

> `agent_name` 是 **`openvox`**（v0.3.0+；v0.2.x 是 `openz`）：LiveKit 派单表按 `openvox` 注册，详见 [`shared/room-naming.md`](./shared/room-naming.md)。

---

## 2. 核心命令矩阵

### 2.1 `openvox`（Python worker CLI）

| 命令 | 用途 | 退出码 |
|---|---|---|
| `openvox init [--provider hermes\|agentd]` | 写 / 更新 `~/.openvox/config.json`，选 LLM 后端 | 0 / 2 |
| `openvox start [--yes]` | 拉起所选后端 + LiveKit worker（前台运行） | 0 / 1 / 2 |
| `openvox stop` | 停受管的 backend 进程 | 0 / 1 |
| `openvox status [--json]` | 报告各 provider 状态 | 0 / 1 |
| `openvox doctor hermes` | 诊断 Hermes readiness（`hermes --version` + HTTP `/health` + `/v1/models`） | 0 / 1 |
| `openvox hermes setup [--yes]` | 配置 Hermes api_server（写入 `~/.hermes/`） | 0 / 1 / 2 |

退出码语义：**0** 成功，**1** 运行时错误（backend 启动失败），**2** 配置 / 参数错误。

### 2.2 `agentd`（Node daemon CLI）

| 命令 | 用途 |
|---|---|
| `agentd --check` | 输出启动日志后退出（验证 build 与配置） |
| `agentd` | 启动 daemon，监听 8787 端口（默认） |
| `agentd --config <path>` | 指定 `~/.agentd/config.json` 之外的配置路径 |

REST 路由：

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` / `/healthz` | liveness + provider 数量 |
| `GET` | `/v1/models` | 列出 provider（id 格式 `agentd/<provider>`） |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions，支持 `stream: true` SSE |
| `GET` | `/v1/sessions` | 列出活跃 session |
| `DELETE` | `/v1/sessions/:id` | 终止 session |

详见 [`apps/agentd/README.md`](./apps/agentd/README.md)。

### 2.3 `lk`（LiveKit CLI）

| 命令 | 用途 |
|---|---|
| `lk dispatch create --dev --room <name> --agent-name <n>` | 派 agent 到房间（本地 dev） |
| `lk token create --dev --room <name> --identity <id> --join` | 签客户端 token（带 join URL） |
| `lk room join <room> --identity <id> --dev --publish <file>` | CLI 端进房推流（e2e 测试用） |

### 2.4 `task`（Taskfile 主编排）

```bash
task dev:infra           # 起 LiveKit Server
task dev:agent           # 起 voice-agent worker（前台）
task dev:client          # 起 Flutter 客户端
task dev:up              # 起 LiveKit + agent（不含 client）
task dev:down            # 停 LiveKit
task build:cli           # 构建两个 CLI
task build:client        # 构建 Flutter android + ios
task install:cli         # 构建 + 全局安装两个 CLI
task release:check       # 输出每个 app 的当前版本
task --list              # 看完整列表
```

`task` 是 `go-task`（macOS `brew install go-task` / Linux 见 go-task.dev）；细节见 [`tooling/Taskfile.yaml`](./tooling/Taskfile.yaml)。

---

## 3. 配置速查

OpenVox 有三处配置，按作用域从大到小：

### 3.1 `~/.openvox/config.json`（worker + 后端）

由 `openvox init` 生成；`openvox start` 时读取。结构：

```jsonc
{
  "livekit": {
    "url": "ws://localhost:7880",
    "api_key": "devkey",
    "api_secret": "secret",
    "agent_name": "openvox"          // 派单时用（v0.3.0+）
  },
  "volcengine": {
    "stt": { "app_id": "...", "access_token": "..." },
    "tts": { "app_id": "...", "access_token": "..." }
  },
  "llm": { "provider": "hermes" },   // 或 "agentd"
  "hermes": {
    "api_base": "http://127.0.0.1:8642/v1",
    "api_key": "",
    "model": "hermes-default"
  },
  "agentd": {
    "api_base": "http://127.0.0.1:8787/v1",
    "api_key": "",
    "model": "agentd/claude"
  }
}
```

字段含义详见 [openwiki/configuration/config-loader.md](./openwiki/configuration/config-loader.md)。

### 3.2 `~/.agentd/config.json`（仅 agentd 后端）

由 `agentd` 首次启动自动创建；可手动编辑。结构：

```jsonc
{
  "port": 8787,
  "host": "127.0.0.1",
  "sessionTtlSeconds": 1800,
  "maxConcurrentPerProvider": 4,
  "rateLimit": { "max": 60, "windowMs": 60000 },
  "auth": { "tokens": ["secret-1"] },
  "providers": [
    {
      "id": "my-agent",
      "label": "Custom ACP Agent",
      "command": "/usr/local/bin/my-acp-cli",
      "args": ["--serve"],
      "protocol": "acp"
    }
  ]
}
```

详见 [`apps/agentd/README.md` § Configuration](./apps/agentd/README.md#configuration)。

### 3.3 `apps/voice-client/.env`（Flutter 客户端）

由 `apps/voice-client/.env.example` 复制而来：

```bash
LIVEKIT_SANDBOX_ID=<your-sandbox-id>   # 仅 dev 用；生产改用自建 token 服务
```

### 3.4 LiveKit Server（Docker）

`infra/docker-compose.yml` 起 `livekit/livekit-server:latest`，端口映射：

| 端口 | 协议 | 用途 |
|---|---|---|
| 7880 | TCP | HTTP signaling + API |
| 7881 | TCP | TCP fallback |
| 7882 | TCP + UDP | WebRTC media |

本地 dev 用 `--dev` 模式启动；生产部署详见 [CONTRIBUTING.md § 发布流程](./CONTRIBUTING.md)。

---

## 4. 典型工作流

### A. 本地端到端调试（hermes 后端，默认）

适合改 Python 代码 / prompt / STT/TTS 配置时。

```bash
# 一次性：开 4 个终端
# T1: LiveKit
(cd infra && docker compose up)

# T2: worker（前台，看日志）
openvox start --yes

# T3: 派单 + 客户端 token
lk dispatch create --dev --room demo --agent-name openvox
lk token create --dev --room demo --identity alice --join

# T4: Flutter 客户端
(cd apps/voice-client && flutter run)

# 改代码 → T2 Ctrl-C → openvox start --yes → 重测
```

### B. 切到 agentd 后端

适合把 Claude Code / Codex / OpenClaw 当 LLM 时。

```bash
# (1) 装 agentd（如果 ./scripts/install.sh --no-node 走过）
(cd apps/agentd && pnpm install && pnpm build)

# (2) 起 agentd（前台或后台）
agentd                     # 默认监听 8787

# (3) 切后端
openvox init --provider agentd

# (4) 起 worker
openvox start --yes
# 看到 "[LLM] agentd/claude" 之类的日志 → 切成功
```

### C. Android 真机调试

适合 Flutter 客户端 UI / LiveKit room 行为调试。

```bash
# (1) USB 连 Android 设备，开启 USB 调试
adb devices                          # 应看到设备 serial

# (2) 跑 Flutter（自动选已连设备）
(cd apps/voice-client && flutter run -d <device-id>)

# (3) 客户端连本地 LiveKit
# .env 里 LIVEKIT_SANDBOX_ID 留空；改用 lk token create 拿 join URL 拷给客户端

# 出包（debug APK）
./tooling/scripts/build-client.sh android
# → apps/voice-client/build/app/outputs/flutter-apk/app-debug.apk
```

### D. 打 GitHub Release

适合发版时。

```bash
# (1) 确认版本
task release:check
# agentd         0.1.0
# openvox        0.2.0
# voice-client   0.2.0+1

# (2) 推 tag（v*.*.* 格式触发 release.yml）
git tag v0.2.0
git push origin v0.2.0

# (3) 看 CI: .github/workflows/release.yml 跑 4 个 build job
#     - agentd 三平台 matrix
#     - openvox 三平台 matrix
#     - Android APK (debug + release)
#     - iOS .app (simulator + device)

# (4) 校验产物
gh release view v0.2.0
```

Android 正式签名需要先上传 keystore 到 Secrets：详见 [README.md § 发布到 GitHub Release](./README.md#发布到-github-release) 与 [CONTRIBUTING.md § Android 签名](./CONTRIBUTING.md)。

### E. 跑 e2e 测试

适合改 worker 逻辑后回归验证。

```bash
# (1) 单元测试（无外部依赖，~46 个）
(cd apps/voice-agent && pytest tests/test_process_runtime.py \
                                  tests/test_llm_provider.py \
                                  tests/test_config.py \
                                  tests/test_hermes_runtime.py \
                                  tests/test_start_script_contract.py -v)

# (2) e2e 测试（需要 LiveKit + 火山引擎凭证）
(cd apps/voice-agent && ./scripts/run_tests.sh e2e)

# (3) agentd 验收
(cd apps/agentd && AGENTD_AUTO_START=1 ./scripts/verify.sh)
```

更多测试约定见 [CONTRIBUTING.md § 测试](./CONTRIBUTING.md)。

---

## 5. 故障排查

> 详细原因与解决方案见 [`apps/voice-agent/CLAUDE.md` § 已知坑](./apps/voice-agent/CLAUDE.md) 与 [openwiki/quickstart.md § 已知坑索引](./openwiki/quickstart.md)。

### 5.1 启动类

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `openvox start --yes` 报 `ConfigError: ~/.openvox/config.json missing` | 未跑 `openvox init` | `openvox init` 后重试 |
| `openvox start --yes` 报 `address already in use (8081)` | worker IPC 端口残留 | `lsof -ti:8081 \| xargs kill -9`（macOS / Linux）/ Windows：`netstat -ano \| findstr :8081` + `taskkill /PID <pid> /F` |
| `openvox status` 卡在 `hermes --version` 探测 | 本机 `hermes` 挂起 | 临时把 `~/.openvox/config.json` 的 `llm.provider` 改成 `agentd` 或用 `OPENVOX_CONFIG=/path/to/none.json` 跳过 |
| `agentd` 报 `Cannot find module 'dist/index.js'` | 未 build | `(cd apps/agentd && pnpm build)` |
| `flutter run` 报 `CocoaPods could not find compatible versions` | pod 版本过旧 / lockfile 漂移 | `(cd ios && pod install --repo-update)` |

### 5.2 语音类

| 症状 | 可能原因 | 处理 |
|---|---|---|
| 客户端连上但 agent 不响应 | worker 没派到这个房间 | 确认 `lk dispatch create --agent-name openvox` 与 `~/.openvox/config.json` 的 `livekit.agent_name` 一致 |
| agent 应答但听不见 | Volcengine TTS 凭证错 | 看 worker 日志的 `[TTS]` 行；检查 `~/.openvox/config.json` 的 `volcengine.tts.access_token` |
| agent 不说话（无 STT 结果） | Volcengine STT AppID 未开通流式识别大模型 | 到火山引擎控制台开通；否则 STT WebSocket 403 |
| 听到对方说话但识别文本错乱 | 采样率 / 编码不匹配 | 确认 LiveKit 音频 track 是 16 kHz PCM；客户端不要 publish 48 kHz 设备原始流 |

### 5.3 配置类

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `openvox init` 报 `python-dotenv not installed` | 没跑 `./scripts/install.sh` 或 venv 没激活 | `source .venv/bin/activate && ./scripts/install.sh` |
| 改了 `~/.openvox/config.json` 不生效 | 进程内 Config 单例缓存 | `openvox stop && openvox start --yes` |
| agentd 报 `auth.tokens` 401 | 客户端没带 bearer | `Authorization: Bearer <token>` 头；本地调试可把 `auth.tokens` 设为 `[]` |

### 5.4 网络 / 端口

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `lk dispatch create` 报 `connection refused :7880` | LiveKit 容器没起 | `(cd infra && docker compose up -d)` |
| `agentd` 报 `EADDRINUSE :8787` | 端口被占 | `lsof -ti:8787 \| xargs kill -9` 或改 `~/.agentd/config.json` 的 `port` |
| Flutter 客户端连不上 LiveKit（用 sandbox 时） | `LIVEKIT_SANDBOX_ID` 错或过期 | 到 LiveKit Cloud Settings → Options 复制最新 sandbox id 写到 `.env` |

---

## 6. 交叉引用

| 你想… | 看哪里 |
|---|---|
| 装环境 | [INSTALLATION.md](./INSTALLATION.md) |
| 改代码 / 提 PR | [CONTRIBUTING.md](./CONTRIBUTING.md) |
| 理解系统设计 | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| 找运行时细节 / 配置字段全表 / 已知坑 | [openwiki/](./openwiki/)（CI 每日刷新） |
| 查 Flutter 客户端 API | [LiveKit Agents Flutter docs](https://docs.livekit.io/agents/start/voice-ai/) |
| 查 LiveKit CLI | [`lk` docs](https://docs.livekit.io/home/cli/cli-setup/) |

---

## 7. 下一步

- 改代码？看 [CONTRIBUTING.md](./CONTRIBUTING.md) —— 分支约定、PR review（特别是 `shared/` 必须双 app review）、测试、发布流程。
- 出问题？先看 §5 故障排查表，再看 [`apps/voice-agent/CLAUDE.md` § 已知坑](./apps/voice-agent/CLAUDE.md) 与 [openwiki/quickstart.md § 已知坑索引](./openwiki/quickstart.md)。
- 想理解"为什么这样设计"？[ARCHITECTURE.md](./ARCHITECTURE.md) 与 [openwiki/architecture/](./openwiki/architecture/)。
