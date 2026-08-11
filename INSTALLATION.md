# INSTALLATION — OpenVox

> 把 OpenVox 开发环境从零装到能跑「[USAGE.md § 5 分钟 walk-through](./USAGE.md#5-分钟-walk-through)」的所有前置。

---

## 1. 前置依赖一览

OpenVox 由 **Python worker + Flutter 客户端 + LiveKit Server** 三部分组成。下表按"必装 / 选装"分组。

### 必装（缺一不可）

| 工具 | 最低版本 | 用途 | macOS | Linux | Windows |
|---|---|---|---|---|---|
| Git | 2.30+ | 拉代码 | `brew install git` | 系统包管理器 | <https://git-scm.com/download/win> |
| Python | 3.10 | voice-agent worker（`openvox` CLI） | `brew install python@3.11` | 系统包管理器 / pyenv | <https://python.org/downloads/> |
| Docker | 24+ | 本地 LiveKit Server | Docker Desktop | Docker Engine + Compose plugin | Docker Desktop |
| `lk` (livekit-cli) | latest | 派单 / token / room join | `brew install livekit-cli` | 参见 LiveKit 文档 | `winget install LiveKit.CLI` |

### 选装（按需）

| 工具 | 最低版本 | 用途 | macOS | Linux | Windows |
|---|---|---|---|---|---|
| Node | 20+ | `agentd` 后端（ACP → OpenAI REST） | `brew install node@20` | 系统包管理器 / nvm | <https://nodejs.org/> |
| pnpm | 9+ | agentd 推荐包管理器 | `npm i -g pnpm` | `npm i -g pnpm` | `npm i -g pnpm` |
| Flutter | 3.24+ | voice-client（iOS/Android/Web/Mac） | <https://docs.flutter.dev/get-started/install/macos> | <https://docs.flutter.dev/get-started/install/linux> | <https://docs.flutter.dev/get-started/install/windows> |
| Android SDK | API 34 | Android 客户端打包 | Android Studio | Android Studio | Android Studio |
| Xcode | 15+ | iOS 客户端打包 | App Store | — | — |
| jq | 1.6+ | 解析 / 调试 JSON（运维辅助） | `brew install jq` | 系统包管理器 | `winget install jqlang.jq` |

> **Hermes 后端（默认 LLM）**与 `openvox` 同进程管理；首次 `openvox init` 会询问是否启用，不需要单独装。详见 [USAGE.md § 选后端](./USAGE.md)。
>
> **iOS / Android 客户端只能在 macOS 上完整构建**。Windows / Linux 主机可以开发 Python agent 与跑 pytest，但客户端构建需要 macOS。

---

## 2. 一键安装（推荐）

### macOS / Linux

```bash
git clone <repo-url> openvox
cd openvox
./scripts/install.sh
```

跳过可选组件：

```bash
./scripts/install.sh --no-flutter     # 不装 Flutter / voice-client 依赖
./scripts/install.sh --no-node        # 不装 agentd 后端
./scripts/install.sh --no-livekit     # 不启动 LiveKit Server 容器
./scripts/install.sh --python /opt/homebrew/bin/python3.11   # 指定 Python
```

### Windows（PowerShell 5.1+）

```powershell
git clone <repo-url> openvox
cd openvox
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

跳过可选组件：

```powershell
.\scripts\install.ps1 -NoFlutter
.\scripts\install.ps1 -NoNode
.\scripts\install.ps1 -NoLiveKit
.\scripts\install.ps1 -Python C:\Python311\python.exe
```

> 两个脚本都是**幂等**的：第二次运行会复用现有 `.venv`、跳过已装好的包、只补齐缺失项。

---

## 3. 手动安装（一步步来）

> 仅当你需要更精细控制（CI、容器、企业代理、自定义路径）时走手动安装。普通开发请用 §2 一键脚本。

### 3.1 拉代码

```bash
git clone <repo-url> openvox
cd openvox
```

### 3.2 Python venv

```bash
python3.11 -m venv .venv
source .venv/bin/activate             # macOS / Linux
# .venv\Scripts\Activate.ps1          # Windows PowerShell
python -m pip install --upgrade pip
```

### 3.3 装 openvox worker

```bash
pip install -e ./apps/voice-agent --no-deps
pip install -e ./apps/voice-agent/plugins/livekit-plugins-volcengine --no-deps
```

> **`--no-deps` 必须保留**。vendored 的 `livekit-plugins-volcengine` 钉了 `livekit-agents==1.5.4`；不加 `--no-deps` 会把宿主降到 1.5.4，拆掉 `[otel, silero, turn-detector]` extras。详见 [`apps/voice-agent/CLAUDE.md` § 已知坑](./apps/voice-agent/CLAUDE.md)。

### 3.4 LiveKit Server（Docker）

```bash
cd infra
docker compose up -d
curl -sf http://localhost:7880 >/dev/null && echo "LiveKit up"
```

### 3.5 Flutter 客户端（可选）

```bash
cd apps/voice-client
cp .env.example .env                  # 占位值，按需覆盖
flutter pub get
flutter run                           # 调试模式
```

### 3.6 agentd 后端（可选）

```bash
cd apps/agentd
pnpm install --frozen-lockfile
pnpm build                             # 产出 dist/index.js
```

---

## 4. 环境变量

OpenVox 与 LiveKit 的交互通过环境变量驱动。完整清单见 [`shared/livekit-env.example.env`](./shared/livekit-env.example.env)；以下是启动 worker 必需的最小集：

| 变量 | 说明 | 示例 |
|---|---|---|
| `LIVEKIT_URL` | LiveKit WebSocket URL | `ws://localhost:7880` |
| `LIVEKIT_API_KEY` | LiveKit API key（本地 dev 默认 `devkey`） | `devkey` |
| `LIVEKIT_API_SECRET` | LiveKit API secret（本地 dev 默认 `secret`） | `secret` |

> `openvox init` 会把上述变量与 `volcengine.stt/tts.access_token`、`hermes.api_base` 一起写到 `~/.openvox/config.json`，不需要手动 export。`openvox start --yes` 启动时会自动 export 给 LiveKit worker。

### agentd 专属

| 变量 | 说明 | 默认 |
|---|---|---|
| `AGENTD_PORT` | agentd 监听端口 | `8787` |
| `AGENTD_HOST` | agentd 监听地址 | `127.0.0.1` |
| `AGENTD_LOG_LEVEL` | 日志级别（debug / info / warn / error） | `info` |

---

## 5. 安装后验证

跑完 §2 或 §3 之后，按顺序验证：

```bash
# (1) Python 端
.venv/bin/python -c "import openvox_worker; print('openvox_worker OK')"
openvox --help                  # 应打印子命令列表

# (2) LiveKit Server（如果选了 docker compose up -d）
docker ps | grep livekit         # 应看到 voice-assistant-livekit-1
curl -sf http://localhost:7880 >/dev/null && echo "LiveKit OK"

# (3) agentd 后端（如果选了 Node 路径）
agentd --check                   # 应输出启动日志后退出
curl -s http://127.0.0.1:8787/v1/models | jq '.[0]'   # 需要 jq

# (4) Flutter 客户端（如果装了 Flutter）
flutter doctor                   # 应全部对勾

# (5) 一键烟测
openvox init                     # 写 ~/.openvox/config.json
openvox start --yes              # 应自动拉起 backend + LiveKit worker
# Ctrl-C 停掉
openvox stop                     # 停 backend
```

任何一步异常，先看 [USAGE.md § 故障排查](./USAGE.md#故障排查)，再回头检查本表。

---

## 6. 故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `./scripts/install.sh: python not found` | 系统未装 Python 或 PATH 不含 `python3` | `brew install python@3.11`（macOS）/ `apt install python3.11`（Linux）/ `winget install Python.Python.3.11`（Win）并重开终端 |
| `pip install -e ./apps/voice-agent` 报 `ImportError: livekit.agents[otel]` | 漏了 `--no-deps` | 加 `--no-deps` 重装 |
| `docker compose up -d` 报 `bind: address already in use` | 7880 被占用 | `lsof -ti:7880 \| xargs kill -9`（macOS / Linux）；Windows：`netstat -ano \| findstr :7880` + `taskkill /PID <pid> /F` |
| `flutter run` 报 `CocoaPods could not find ...` | 未 `pod install` 或 pod 版本过旧 | `cd ios && pod install --repo-update` |
| `openvox start --yes` 报 `address already in use (8081)` | worker IPC 端口残留 | `lsof -ti:8081 \| xargs kill -9`（macOS / Linux）；Windows：`netstat -ano \| findstr :8081` + `taskkill /PID <pid> /F` |
| Volcengine STT 报 403 | AppID 未开通「流式语音识别 大模型」 | 到火山引擎控制台给 AppID 开通流式识别大模型权限 |

更多坑见 [`apps/voice-agent/CLAUDE.md` § 已知坑](./apps/voice-agent/CLAUDE.md) 与 [openwiki/quickstart.md § 已知坑索引](./openwiki/quickstart.md)。

---

## 7. 卸载

按"装了哪些拆哪些"的原则：

```bash
# 全局 CLI（如果通过 tooling/scripts/install-cli.sh 装过）
pipx uninstall openvox          # 或：pip uninstall openvox
npm uninstall -g agentd

# 本仓库的 .venv 与构建产物
rm -rf .venv
rm -rf apps/voice-agent/dist apps/agentd/dist
rm -rf apps/voice-client/build

# LiveKit 容器
(cd infra && docker compose down -v)

# 用户态配置
rm -rf ~/.openvox ~/.agentd
```

代码本身：

```bash
cd .. && rm -rf openvox
```

---

## 8. 下一步

- 看 [USAGE.md](./USAGE.md) 拿 5 分钟 walk-through + 命令矩阵 + 典型工作流。
- 改代码前看 [CONTRIBUTING.md](./CONTRIBUTING.md)。
- 想理解整个系统怎么搭的看 [ARCHITECTURE.md](./ARCHITECTURE.md)。
- 找具体运行时细节 / 配置字段 / 已知坑看 [openwiki/](./openwiki/)（由 CI 每日刷新）。
