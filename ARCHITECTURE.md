# ARCHITECTURE — OpenVox

> 描述 OpenVox 的**公共可观察边界**：子系统、数据流、职责划分、关键模块、集成契约、部署面。运行时内部细节 / monkey-patch 解释 / 配置字段全表见 [openwiki/architecture/](./openwiki/architecture/)。

---

## 1. 系统边界

### OpenVox 是什么

- 一个**实时中文语音 Agent 平台**：用户对着客户端说话 → 系统把中文翻成文字 → 调 LLM 生成回复 → 把回复合成语音 → 在房间里回放
- 由 **3 个可独立部署的子系统**组成：Flutter 客户端、Python LiveKit worker、Node agentd daemon
- 通过 **LiveKit Server**（Docker 或 LiveKit Cloud）做信令 / SFU / 房间管理
- LLM 后端**可插拔**：本地 Hermes gateway / agentd 桥接的 Claude Code / Codex / OpenClaw 等 ACP CLI

### OpenVox 不是什么

- **不是云 SaaS** —— 所有组件都是本地可跑，infra 仅依赖 LiveKit Server + 火山引擎凭证
- **不是端到端 ASR/TTS 模型** —— STT/TTS 全部委托给火山引擎，OpenVox 不训练模型
- **不是 LLM 训练框架** —— LLM 后端是外部服务（Hermes / agentd 桥接），OpenVox 只负责编排
- **不是 LiveKit 替代品** —— OpenVox 是 LiveKit Agents 框架的应用层

---

## 2. 高层架构（4 块子系统）

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Flutter 客户端                               │
│                       apps/voice-client/                              │
│         iOS · Android · Web · macOS  (LiveKit Flutter SDK)           │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ WebRTC (audio + signaling)
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       LiveKit Server (SFU)                            │
│                      infra/docker-compose.yml                         │
│         :7880 HTTP · :7881 TCP · :7882 UDP                            │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ Agent job dispatch
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    voice-agent worker (Python)                        │
│                    apps/voice-agent/                                  │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐      │
│   │ STT      │ ─→ │ LLM      │ ─→ │ TTS      │ ─→ │ LiveKit  │      │
│   │ Volcengine│    │ Hermes / │    │ Volcengine│   │ audio out│      │
│   │ 大模型   │    │ agentd   │    │ 豆包 V3   │   │          │      │
│   └──────────┘    └──────────┘    └──────────┘    └──────────┘      │
│   OpenAI-compat endpoint · livekit-plugins-volcengine                 │
└─────────────┬───────────────────────────────────┬────────────────────┘
              │ chat/completions (OpenAI-compat)  │
              ▼                                   ▼
   ┌─────────────────────┐           ┌─────────────────────────┐
   │  Hermes (Python)    │           │  agentd (Node + Fastify) │
   │  本地 LLM gateway   │           │  ACP → OpenAI REST 桥接  │
   │  :8642/v1           │           │  :8787/v1                │
   └─────────────────────┘           └────────────┬────────────┘
                                                  │ stdio JSON-RPC
                                                  ▼
                                       ┌─────────────────────────┐
                                       │  ACP CLI (Claude Code / │
                                       │  Codex / OpenClaw / ...) │
                                       └─────────────────────────┘
```

四块子系统的**职责硬边界**：

| 子系统 | 做什么 | 不做什么 |
|---|---|---|
| Flutter 客户端 | 采集 / 播放音频、UI、连房 | 任何 STT / LLM / TTS |
| LiveKit Server | 房间、信令、WebRTC SFU、派单 | 业务逻辑、LLM 调用 |
| voice-agent worker | STT → LLM → TTS 编排、session 生命周期 | LLM 模型本身、UI、客户端分发 |
| agentd daemon | 把 ACP 兼容 CLI 桥成 OpenAI REST | 跑 LLM 模型、连 LiveKit |

---

## 3. 数据流（一次语音 round-trip）

```
[1] 用户按住 Flutter 客户端的麦克风
    │
    │ WebRTC audio track
    ▼
[2] LiveKit Server 把音频帧转发给订阅了该房间的 worker
    │
    │ PCM frames
    ▼
[3] voice-agent worker: STT 接收音频
    Volcengine 大模型 WebSocket → 流式文本（utterances[i].definite=True）
    │
    │ final text
    ▼
[4] voice-agent worker: LLM 调用
    openai.LLM.chat(messages=[system, user])   ← 通过 OpenAI-compat 端点
       │
       │ （hermes 后端）→ 本地 Hermes api_server :8642/v1
       │ （agentd 后端）→ agentd :8787/v1 → ACP CLI 子进程
       │
       │ streaming assistant delta
       ▼
[5] voice-agent worker: TTS 合成
    Volcengine TTS 豆包 V3 chunked HTTP → 音频块
    │
    │ audio chunks
    ▼
[6] voice-agent worker 把音频块 publish 回 LiveKit room
    │
    │ WebRTC audio track
    ▼
[7] Flutter 客户端订阅音频，UI 播放
```

**关键时序不变式**（违反会导致体验崩坏）：

1. **STT 必须先出最终识别文本**（`definite=True`）才喂给 LLM —— 不允许流式增量喂，避免半句话被 LLM 抢答
2. **LLM 必须流式**（`stream=True`）—— 端到端延迟的关键在 LLM TTFT（time-to-first-token）
3. **TTS 整句合成**而不是流式增量 —— 减少拼接痕迹；延迟在可接受范围（Volcengine 整句 < 500ms）

---

## 4. 子系统职责边界（详细）

### 4.1 voice-client（Flutter）

- 入口：`apps/voice-client/lib/main.dart`
- 核心：`livekit_client.Session` + `RoomContext` + `MediaDeviceContext`
- 配置：`apps/voice-client/.env`（仅 `LIVEKIT_SANDBOX_ID`，开发用；生产改用自建 token 服务）
- 出包：`./tooling/scripts/build-client.sh android|ios|all`（详见 [README.md § 构建 Flutter 客户端](./README.md#本地打包与全局安装)）
- **不做**：STT/TTS/LLM；只做音视频 I/O + 房间协议

### 4.2 LiveKit Server

- 入口：`infra/docker-compose.yml`（镜像 `livekit/livekit-server:latest`，`--dev` 模式）
- 端口：7880（HTTP signaling）/ 7881（TCP fallback）/ 7882（WebRTC media TCP+UDP）
- 生产：替换为 LiveKit Cloud 或自建集群（详见 [CONTRIBUTING.md § 发布流程](./CONTRIBUTING.md)）
- **不做**：任何业务逻辑；只做 SFU + 房间 + 派单

### 4.3 voice-agent worker（Python）

- 入口：`apps/voice-agent/openvox_worker/main.py`
- 包名：`openvox_worker`（`pip install -e .` 后由 `openvox = openvox_worker.cli:main` 暴露 CLI）
- 配置：`~/.openvox/config.json`（loader 在 `apps/voice-agent/openvox_worker/config.py`）
- 关键模块（按生命周期顺序）：

| 模块 | 职责 |
|---|---|
| `config.py` | `~/.openvox/config.json` loader，带点路径 `require` / `get`；`OPENVOX_CONFIG` 环境变量覆盖 |
| `llm_provider.py` | 按 `llm.provider` 选择 LLM 端点（hermes / agentd / claude / codex(planned) / openclaw(planned)），构造 `openai.LLM` |
| `process_runtime.py` | 通用进程托管（`ProcessSupervisor` + `OwnedProcess`），start 写 `runtime-<name>.json`（0600），stop 先验权再 SIGTERM |
| `hermes_runtime.py` | Hermes gateway 生命周期（`inspect()` / `start()` / `ensure_ready(auto_start=True)`），三重探测：`hermes --version` + HTTP `/health` + `/v1/models` |
| `agentd_runtime.py` | agentd 子进程生命周期：`ensure_config` 把 Python Config 投射为 JSON；`start` 检查 `node` + `pnpm install/build` + `node dist/index.js --config <projection>` + 健康检查；失败回滚 |
| `cli.py` | `openvox init/start/stop/status/doctor/hermes` 子命令编排（详见 §5.1） |
| `main.py` | LiveKit Agent + WorkerOptions + 5 处 monkey-patch（详见 §5.2） |

### 4.4 agentd daemon（Node + Fastify）

- 入口：`apps/agentd/src/index.ts` → `daemon.ts` → `api/server.ts`
- 配置：`~/.agentd/config.json`（zod 验证 + dot-path 合并）
- REST 路由：`/health` `/healthz` `/v1/models` `/v1/chat/completions` `/v1/sessions` `DELETE /v1/sessions/:id`
- 鉴权：`Authorization: Bearer <token>`（来自 `auth.tokens`，未配置则全开）
- 限流：`@fastify/rate-limit`，key on bearer token 或 IP
- **不做**：连 LiveKit、跑 LLM；只把 ACP CLI 桥成 OpenAI REST

### 4.5 LLM 后端（hermes / agentd / 未来更多）

| 后端 | 何时选 | 何时不选 |
|---|---|---|
| **hermes**（默认） | 纯 Python、轻量、本地调试 | 需要 Claude / GPT 等高质量模型时 |
| **agentd → claude** | 需要 Claude 质量，且能跑 Claude Code CLI | 不想装 Node 栈时 |
| **agentd → codex / openclaw** | （planned） | 当前未实现 |
| 自建 OpenAI-compat | 把 `api_base` 指向任何 OpenAI SDK 兼容服务 | — |

---

## 5. 关键模块与 file:line 引用

> 所有引用基于本仓库当前 main 分支。行号会漂移；引用以"模块级职责"为锚，精确行号作为辅助。

### 5.1 voice-agent CLI 编排（`apps/voice-agent/openvox_worker/cli.py`）

| 子命令 | 入口 | 说明 |
|---|---|---|
| `openvox init` | `cli.py:60+` | 交互式写入 `~/.openvox/config.json`；provider 候选 = `USER_FACING_PROVIDERS = ("hermes", "claude", "codex", "openclaw")`（注意 `agentd` 是**内部基础设施**，故意不暴露给用户选择） |
| `openvox start --yes` | `cli.py` `orchestrate_start()` | 拉起所选 backend → 健康检查 → 启 LiveKit worker；任一步失败回滚 |
| `openvox stop` | `cli.py` `orchestrate_stop()` | 仅停受管的 agentd；外部 Hermes gateway 不动 |
| `openvox status [--json]` | `cli.py` | 报告各 provider 状态；`codex` / `openclaw` 标 `planned` |
| `openvox doctor hermes` | `cli.py` | 打印 `HermesRuntime.inspect()` |
| `openvox hermes setup [--yes]` | `cli.py` | 驱动 `HermesConfigurator`（默认 preview，`--yes` apply） |

退出码语义（写在模块 docstring 第 22-23 行）：`0` 成功 / `2` 用户错误 / `1` 运行时错误。

### 5.2 voice-agent worker 入口（`apps/voice-agent/openvox_worker/main.py`）

5 处 monkey-patch（**不可删**，每个都有对应原因）：

| 位置 | patch | 原因 |
|---|---|---|
| `main.py:24-97` | `_FilterNoneChoices` + `_safe_create` | 过滤 Hermes usage-only chunk（`chunk.choices is None`），否则 `livekit-plugins-openai` 抛 `TypeError`；同时累积 `[LLM-TEXT]` marker 给 e2e 测试 |
| `main.py:99-130+` | `livekit.agents.cli.log.setup_logging = no-op` | 框架默认会叠 JSON handler，不替换会出现每条日志打两遍 |
| `main.py:130-160+` | `volcengine.STT._process_stream_event` wrap | 在 `utterances[0].definite=True` 时打 `[用户语音] <text>`，给控制台加可视锚点 |
| `main.py:160-190+` | `volcengine.STT._run` wrap | 吞 `asyncio.CancelledError`（`livekit-agents 1.6.x` 子进程拆时未 await 的 `_GatheringFuture` 会污染日志） |
| `main.py:190-220+` | `volcengine.TTS._run` wrap | 同上 |

完整 line range 详解见 [openwiki/architecture/overview.md § 模块加载时安装的 patch](./openwiki/architecture/overview.md)。

### 5.3 agentd daemon 启动序列（`apps/agentd/src/daemon.ts`）

```typescript
// daemon.ts:33-91 — startDaemon(configPath?)
const cfg = applyEnvOverrides(await loadConfig(configPath));  // 34
const sessions = new SessionManager();                          // 37
await sessions.load();                                          // 38
const discovered = await discoverProviders();                   // 40
const registry = new ProviderRegistry();                        // 41
for (const p of cfg.providers ?? []) registry.registerCustom(p);// 42-44
const { added, skipped } = registry.load(discovered);           // 45
// long-lived providers init + prewarm（49-80）
const server = await buildServer({ cfg, registry, sessions }); // 82
const sweeper = new TtlSweeper(sessions, { ttlSeconds: ... });  // 84
sweeper.start();                                                // 85
await server.listen({ port: cfg.port, host: cfg.host });        // 87
```

**关键不变式**：

- `discoverProviders()` 扫 PATH 找 `claude` / `codex` / `openclaw` 等 ACP CLI；用户在 `~/.agentd/config.json` 配 `providers[]` 可以注册**任意**自定义 ACP CLI
- `TtlSweeper` 每 60s 扫一次 session 列表；超过 `sessionTtlSeconds`（默认 1800s = 30 min）自动清理
- `longLivedProviders` 是带长生命周期子进程的 provider（如 `ClaudeProvider` 持有 claude CLI 子进程池），shutdown 时按 `cfg.maxConcurrentPerProvider` 排空

---

## 6. 集成边界

OpenVox 与外部世界的契约，按"谁调谁"：

| 主调 → 被调 | 协议 | 端点 / 接口 | 备注 |
|---|---|---|---|
| voice-agent → LiveKit | LiveKit Agents SDK | `WorkerOptions(agent_name=...)` | 注册 worker；LiveKit 按 room dispatch |
| LiveKit → voice-agent | Agent job dispatch | `async def entrypoint(ctx)` | 每房间建独立 session |
| voice-agent → Volcengine STT | WebSocket | 大模型流式识别 | AppID 必须开通「流式语音识别 大模型」 |
| voice-agent → Volcengine TTS | HTTP chunked | 豆包 V3 | 整句合成 |
| voice-agent → Hermes | OpenAI Chat Completions | `POST {api_base}/v1/chat/completions` | `stream: true` |
| voice-agent → agentd | OpenAI Chat Completions | `POST http://127.0.0.1:8787/v1/chat/completions` | 同上 |
| agentd → ACP CLI | JSON-RPC stdio | `@agentclientprotocol/sdk@1.2.1` | 每个 provider 一个子进程 |
| voice-client → LiveKit | LiveKit Flutter SDK | `livekit_client.Session` | token 来自 `lk token create` 或自建 token 服务 |
| voice-client → LiveKit Cloud（生产） | Access Token (JWT) | 自建 token 服务 | 参考 `shared/livekit-claims.example.json` |
| voice-client → Voice-Agent | 不直连 | — | 通信完全经 LiveKit 房间（audio track + data channel） |

**契约变更门槛**：

- `shared/` 任何文件改动必须 `apps/voice-agent` **和** `apps/voice-client` 双 app reviewer 勾选（详见 [CONTRIBUTING.md § 黄金法则](./CONTRIBUTING.md)）
- LiveKit Agents SDK 主版本升级需要回归所有 e2e 测试（`AgentSession.__init__` 签名、`RoomInputOptions` 形态等都跨版本变过）
- Volcengine 插件是 vendored 在 `apps/voice-agent/plugins/livekit-plugins-volcengine/`，不通过 PyPI 自动拿；上游变更需要手动同步

---

## 7. 部署 / 发布面

OpenVox 通过 `git tag v*.*.*` 触发 [.github/workflows/release.yml](./.github/workflows/release.yml) 产出 **6 类 artifact**：

| Tag | 平台 | 类型 | 消费者 |
|---|---|---|---|
| `agentd-<v>-linux.tgz` | linux x64 | npm pack | `npm install -g` / `pnpm add -g` |
| `agentd-<v>-macos.tgz` | darwin universal | npm pack | 同上 |
| `agentd-<v>-windows.tgz` | win32 x64 | npm pack | 同上 |
| `openvox-<v>-py3-none-any.whl` | any | Python wheel | `pip install` / `pipx install` |
| `openvox-<v>.tar.gz` | any | Python sdist | 离线 / 镜像 |
| `voice-client-<v>-android-{debug,release}.apk` | android | Flutter APK | adb install / Play Store |
| `voice-client-<v>-ios-{simulator,device}.zip` | iOS | Runner.app 打包 | Xcode / TestFlight |

发布矩阵由 release.yml 的 4 个并行 job 驱动：

1. **`agentd`** job：三平台（linux / macos / windows）matrix 并行
2. **`openvox`** job：Python wheel + sdist 三平台（实际是 any，因为 wheel 是 py3-none-any）
3. **`voice-client-android`** job：debug APK + （如果 Secrets 完整）release APK
4. **`voice-client-ios`** job：simulator slice + device slice（no-codesign）

详情见 [CONTRIBUTING.md § 发布流程](./CONTRIBUTING.md#发布流程) 与 [README.md § 发布到 GitHub Release](./README.md#发布到-github-release)。

---

## 8. 与 [openwiki/architecture/](./openwiki/architecture/) 的边界

两份文档讲的是不同视角，**不重叠**：

| 本文件（ARCHITECTURE.md） | openwiki/architecture/ |
|---|---|
| 公共安全视角：子系统、数据流、集成契约、部署面 | 运行时视角：worker 生命周期、monkey-patch 解释、配置字段全表 |
| 给新读者 / 维护者 / 集成方 | 给正在改 voice-agent 代码的开发者 |
| 静态结构（一次性看完） | 动态行为（按场景跳读） |
| 不含 LiveKit Agents SDK 内部 | 含 `_build_session` / `_prewarm` / `AgentSession` 内部细节 |
| 不展开 monkey-patch 行号 | 精确到 `main.py:24-97` 等 |

**何时看哪个**：

- 想理解"系统长什么样" → 本文件
- 想理解"代码怎么跑起来的" → openwiki/architecture/
- 想理解"为什么这样设计" → openwiki/architecture/overview.md § Source anchors

---

## 9. 关键不变式 / 已知限制

### 不变式（违反会立刻坏）

1. **`agent_name` 是 `openvox`**（v0.3.0+；v0.2.x 是 `openz`）—— LiveKit 派单表按 `openvox` 注册。详见 [`shared/room-naming.md`](./shared/room-naming.md)。
2. **`pip install -e ./apps/voice-agent --no-deps`** —— 不带 `--no-deps` 会拆 extras
3. **`shared/` 改动必须双 app review** —— 改命名 / 协议字段不通知对端会出现 runtime TypeError
4. **`VolcengineAgent.on_enter` 通过 `generate_reply(user_input="打招呼")` 注入占位** —— 因为 Hermes 网关要求至少一条 user 消息

### 已知限制

1. **iOS / Android 客户端只能在 macOS 上完整构建** —— Windows / Linux 只能 dev Python agent + Flutter desktop/web
2. **agentd daemon 不支持 Windows**（`apps/agentd/README.md` § Out of Scope 显式列出）
3. **`openvox start` 会对 hermes 做 `hermes --version` 探测** —— 本机 `hermes` 挂起时 `openvox status` 可能超时
4. **STT AppID 需开通「流式语音识别 大模型」** —— 否则 Volcengine WebSocket 403
5. **worker IPC 端口 8081** —— 崩溃残留需要手动 `lsof -ti:8081 | xargs kill -9`

完整已知坑索引见 [`apps/voice-agent/CLAUDE.md` § 已知坑](./apps/voice-agent/CLAUDE.md) 与 [openwiki/quickstart.md § 已知坑索引](./openwiki/quickstart.md)。

### Backlog（不在本仓库实现的延期项）

| 区域 | 源锚 | 延期原因 |
|---|---|---|
| Docker 打包 / livekit.yaml / start-lan.sh / start-emu.sh | `apps/voice-agent/CLAUDE.md` | 当前 worktree 中未提交；等打包落地再补 |
| Function tools / MCP / persona / skills / memory | `apps/voice-agent/docs/agent-capabilities-extension.md` | 重构中移除；测试锁定 `test_no_agent_persona_import` |
| Qwen Realtime 插件 | `plugins/livekit-plugins-qwen/` | 目录保留但未引用；测试锁定 |
| LiveKit Cloud / 生产部署 | `apps/voice-agent/README.md` | 仓库内暂无生产部署产物 |

---

## 10. 下一步

- 想跑起来 → [USAGE.md](./USAGE.md)
- 想改代码 → [CONTRIBUTING.md](./CONTRIBUTING.md)
- 找具体运行时细节 / 配置字段全表 → [openwiki/](./openwiki/)
- 找入口 → [README.md](./README.md)
