# Changelog

OpenVox 所有面向用户可见的变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 1.1.0；版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

> 本文件**只记面向用户的变更**（CLI / 文档 / 安装脚本 / Release artifact）；运行时细节 / monkey-patch / 配置字段全表见 [openwiki/quickstart.md](./openwiki/quickstart.md) 与 [openwiki/architecture/](./openwiki/architecture/)。
>
> 各 app 独立版本：
> - `openvox`（Python worker）：`apps/voice-agent/pyproject.toml` 的 `version`
> - `agentd`（Node daemon）：`apps/agentd/package.json` 的 `version`
> - `voice-client`（Flutter）：`apps/voice-client/pubspec.yaml` 的 `version`

---

## [v0.3.1] - 2026-08-11

### Fixed

**Release CI 修复（v0.3.0 tag 已推送但 release job 跳过的根因）**

- **`apps/agentd/pnpm-workspace.yaml`** —— 删除。该文件内容是 `allowBuilds: set this to true or false`（字面占位符），pnpm v9+ 校验失败：`ERROR  packages field missing or empty`。agentd 是单 package，不需要 workspace 元数据；`allowBuilds` 若真要配置应放 `package.json#pnpm.allowBuilds`。
- **`voice-client/ios` 构建** —— release.yml iOS job 加 `pod install --repo-update` 步骤。`Podfile.lock` 中 `WebRTC-SDK` 版本可能与 Flutter 解析的 `flutter_webrtc` 新版本不一致（v0.3.0 release run 报 `flutter_webrtc (1.6.0) requires WebRTC-SDK (= 144.7559.09)` 与 lock 中 `137.7151.04` 冲突）。`--repo-update` 在 build 前刷新 lock。

### 3-app 版本 patch bump

- `openvox` (voice-agent)：0.3.0 → 0.3.1
- `agentd`：0.2.0 → 0.2.1
- `voice-client`：1.0.0+15 → 1.0.0+16

### 用户影响

无。v0.3.0 → v0.3.1 是 release-only fix，不影响 API / config / 行为。如果之前没装上 v0.3.0 artifact（因为 release job 跳过），这次应该能正常装上。

---

## [v0.3.0] - 2026-08-11

### ⚠ BREAKING CHANGE

**`agent_name` 从 `openz` 迁移到 `openvox`** —— LiveKit 派单表、worker 注册、Flutter 客户端连 Session 三端必须同步升级。

- **客户端**（Flutter）：`apps/voice-client/lib/livekit_config.dart` 的 `const agentName = 'openz';` → `'openvox'`。app 重发版后客户端连房间用新名。
- **worker**（Python）：`apps/voice-agent/openvox_worker/cli.py` 的 `setdefault("agent_name", "openz")` → `"openvox"`；老 `~/.openvox/config.json` 的 `livekit.agent_name` 字段需手动改为 `openvox`（或重跑 `openvox init`），否则 dispatch 失败。
- **LiveKit Server 派单表**：自建 Server 无需配置（worker 启动时自动注册）；LiveKit Cloud 在控制台把派单表里的 `openz` 改成 `openvox`。

> 历史：项目名 OpenVox，但 v0.2.x 期间 LiveKit 派单表仍登记 `openz`（外部 app 未迁移），所以 worker / 客户端 / 文档都用 `openz`。v0.3.0 起统一。

### Added

**Documentation（顶层文档体系 + CI 自检）**

- **顶层 5 份主文档**：README（10.3 KB，30 秒电梯版）/ INSTALLATION（8.7 KB，跨平台安装）/ USAGE（13.2 KB，5-min walk-through + 4 命令矩阵 + 5 工作流 + 4 故障类）/ CONTRIBUTING（13.3 KB，5 黄金法则 + Conventional Commits + PR 矩阵 + Android 签名）/ ARCHITECTURE（20.6 KB，4 块架构图 + 7 步数据流 + file:line 引用）
- **3 份子区 README**：tooling/README（9.0 KB，Taskfile vs scripts 分工）/ infra/README（5.4 KB，LiveKit docker-compose + dev vs 生产）/ apps/voice-client/README（14.3 KB，从 LiveKit starter 改写为 OpenVox-specific）
- **2 个一键 bootstrap 脚本**：`scripts/install.sh`（macOS/Linux，幂等 5 阶段）+ `scripts/install.ps1`（Windows PowerShell）
- **CHANGELOG.md**（本文件，Keep a Changelog 1.1.0 + SemVer 格式）
- **`tooling/scripts/check-doc-links.sh`** —— bash + perl 验证 102 个内部链接 + GitHub-style CJK 锚点
- **`.github/workflows/docs-check.yml`** —— 独立 CI workflow，触发 `**/*.md` + `tooling/scripts/**`，ubuntu-latest 跑 check-doc-links.sh
- **`tooling/Taskfile.yaml` 新增 task**：`lint`（agentd typecheck + flutter analyze）/ `lint:agentd` / `lint:client` / `docs:check` / `docs:check:strict` / `docs:check:quiet`

### Changed

- **`agent_name` 默认值**：`openvox init` 的 `livekit.agent_name` 兜底从 `openz` 改为 `openvox`（见 BREAKING CHANGE）
- **`shared/room-naming.md`**：权威源更新为 `agent_name = "openvox"`；附录加 `v0.3.0` 迁移说明

### Fixed

- **AGENTS / 已知坑文档同步**：所有 `--agent-name openz` / `agent_name = "openz"` / `agentName = 'openz'` 在 4 顶层文档 + `shared/room-naming.md` + `apps/voice-agent/{README,CLAUDE}.md` + 测试 fixture 全部更新为 `openvox`

### Performance

- **首次语音延迟从 8.4s 降至 ~0.6s**（`b368a34 perf: cut user-perceived first-audio latency from 8.4s to ~0.6s`）—— 通过优化 audio pipeline 启动顺序。

---

## [v0.2.0] - 2026-08-02

OpenVox 第一次正式 monorepo release。3 个 app 在一个仓库协同发布；6 类 release artifact 通过 `git tag v*.*.*` 自动产出。

### Added

**Python worker (`openvox` CLI)**

- **可切换 LLM 后端** —— `llm.provider` 控制，支持 `hermes`（本地 Python gateway）/ `agentd`（Node 桥接）/ `claude`（直连或经 agentd）。
- **统一 CLI 入口** —— `openvox init / start / stop / status / doctor / hermes setup` 6 个子命令编排。
- **交互式 provider 选择** —— `openvox init` 用 questionary 交互选 backend；`--yes` 跳过确认。
- **rich-argparse** —— prettier `--help` 输出。
- **filtered logs** —— `openvox logs [target]` 支持 `--tail` / `--since` / `--grep` 过滤。
- **resilient Hermes status** —— 状态探测带重试，不被瞬时挂起误报。
- **LiveKit 默认值 seed** —— `init` 阶段把 dev LiveKit URL / api_key / api_secret 写入 `~/.openvox/config.json`，避免每次手填。
- **多 backend 探测 / 健康检查** —— `start` 自动确认 backend 就绪后再起 LiveKit worker；任何一步失败回滚。
- **退出码语义** —— `0` 成功 / `1` 运行时错误 / `2` 用户错误（CLI 编排可精确判断失败原因）。

**agentd（Node daemon）**

- **本地 ACP → OpenAI REST bridge** —— 把 Claude Code / Codex / OpenClaw 等 ACP CLI 桥成 `POST /v1/chat/completions`（OpenAI-compat）+ SSE 流。
- **6 个 REST 路由** —— `/health` / `/healthz` / `/v1/models` / `/v1/chat/completions` / `/v1/sessions` / `DELETE /v1/sessions/:id`。
- **三档退出码** —— `--check` 输出启动日志后退出（验证 build）。
- **Bearer auth + rate limit** —— `auth.tokens` 共享密钥 + `@fastify/rate-limit` 按 token / IP 限流。
- **Provider discovery + custom registry** —— 启动时扫 PATH 找 ACP CLI；同时支持 `~/.agentd/config.json` 配 `providers[]` 注册自定义 binary。
- **Session 三层 ID map + TTL sweeper** —— `room_id ↔ agentd_session_id ↔ cli_session_id`；30 min idle 自动清理。
- **CI-friendly** —— 测试不依赖真实 Claude CLI；`AGENTD_AUTO_START=1 ./scripts/verify.sh` 验收。

**voice-client（Flutter）**

- **从 LiveKit Agents Flutter starter 派生 + 大量定制** —— lib/ 拆 9 个子目录（audio / controllers / screens / ui / util / widgets / support / logs / helpers）；自签 HS256 token 替代 LiveKit Cloud Sandbox。
- **三套测试** —— `test/widget_test.dart`（Dart unit）/ `integration_test/vox_e2e_test.dart`（Patrol native）/ `e2e/*.py`（Python idb UI walkthrough 9 阶段）。

**Tooling**

- **Taskfile 主编排** —— `dev:*` / `build:*` / `install:cli*` / `release:check` / `lint`（lint 占位）。
- **5 个 shell 脚本** —— `build-cli.sh` / `install-cli.sh` / `build-client.sh` / `dev-up.sh` / `dev-down.sh`。
- **共享 shell 库** —— `scripts/lib/log.sh`（step/info/warn/err/die/have）+ `scripts/lib/versions.sh`（3 个 app_version）。
- **CI workflow 矩阵** —— `.github/workflows/ci.yml` 4 个 job（agentd / voice-agent / client-android / client-ios）+ `.github/workflows/release.yml` tag-driven release。

**Documentation**

- **顶层 README** —— 项目定位 + 5 分钟快速上手 + 构建/安装/发布。
- **OpenWiki 自动 wiki** —— `openwiki/` 由 `openwiki-update.yml`（`cron: 0 8 * * *`）每日刷新；含 quickstart / architecture / configuration / operations / integrations 5 大类。
- **跨端契约** —— `shared/room-naming.md` / `shared/agent-protocol.md` / `shared/livekit-claims.example.json` / `shared/livekit-env.example.env`。
- **`shared/` 变更门槛** —— 改 `shared/` 必须 `apps/voice-agent` + `apps/voice-client` 双 app review。

### Changed

- **vendored Volcengine 插件** —— `apps/voice-agent/plugins/livekit-plugins-volcengine/` 从外部依赖改为 vendored，钉 `livekit-agents==1.5.4`。
- **拆分 runtime 依赖** —— `livekit-plugins-silero` / `livekit-plugins-turn-detector` / `opentelemetry-*` 拆为顶层声明（LiveKit 1.6+ 不再 extras）。
- **CLI init 不再问 API key** —— LiveKit key / secret 在 `init` 阶段自动 seed；用户不需要手填。
- **init 与 start 解耦** —— 之前 `init` 兼配置 backend，现在 `init` 只写 `~/.openvox/config.json`，`start` 才探测 + 拉 backend。
- **Android release 加 keystore signing** —— 通过 GitHub Secrets（`ANDROID_KEYSTORE_BASE64` 等 4 个）；未配置完整时产物命名为 `*-release-debug-signed.apk` 标识。
- **Flutter 锁文件策略统一** —— 跟踪 `pubspec.lock`（之前未跟踪）。

### Fixed

- **iOS simulator slice** —— `flutter build ios --simulator` 在新 runner 上单独构建（之前和 device slice 串行失败）。
- **iOS device slice** —— `--no-codesign` 在 device runner 上单独构建。
- **Release artifact 命名** —— 统一 flat 命名（之前嵌套目录导致 `gh release upload` 路径错乱）。
- **Release 上传改用 `gh release upload`** —— 之前用 curl 直传二进制，被 GitHub 当 LFS 处理导致损坏。
- **Android keystore 路径** —— 不再放 `apps/voice-client/android/app/`，改走 GitHub Secrets。
- **CI artifacts dir** —— 处理每 runner 独立 `actions/upload-artifact` 工作目录。
- **CI agentd install** —— 解锁 Flutter asset bundling 在 CI runner 上的环境差异。
- **CI action pinning** —— 锁 `actions/checkout` / `actions/setup-node` / `actions/setup-python` 到具体 commit SHA（避免上游漂移）。
- **tooling 本地构建** —— `tooling/scripts/build-*.sh` 在 macOS / Linux / Windows 上一致行为。
- **CLI auto-start backend** —— `start` 不再需要 `--yes` 强制；自动拉 backend。
- **provider 探测去重** —— Claude Code 通过 agentd 暴露为 `agentd/claude`，不再单独 standalone provider。
- **init 拒绝 unready provider** —— 选未就绪的 backend 时给明确错误，不让 `start` 阶段才失败。
- **OpenWiki 中文本地化** —— 把 OpenWiki 模板英文内容翻成中文。

### Removed

- **未使用的 vendored 片段** —— `apps/voice-agent/plugins/` 下的历史 qwen / 旧 livekit-plugins-* 子目录清掉。
- **CLI `--api-key` prompt** —— init 阶段不再问 API key（见 Changed）。
- **iOS / macOS / Linux Pods** —— 不入库（`.gitignore`）。

### Security

- **首次引入 LiveKit DEV credentials** —— `apps/voice-client/lib/livekit_config.dart` 含 DEV-ONLY HS256 API key/secret（注释明确"反编译可提取"）；本地开发可接受，**任何 laptop 外部署必须换自建 token server**。

---

## [v0.1.0] - 2026-07（pre-monorepo）

pre-monorepo 阶段。各 app 独立仓库 + 独立版本号；2026-08 monorepo 重构时合并入本仓库。

详见各 app 的 pre-monorepo 历史（git log `248bcd3` 之前的 commit 来自原始仓库）。

---

## 版本号约定

OpenVox 三个 app **各自**有 version 字段：

| App | 字段位置 | 约束 |
|---|---|---|
| `openvox`（voice-agent） | `apps/voice-agent/pyproject.toml` | SemVer（`MAJOR.MINOR.PATCH`） |
| `agentd` | `apps/agentd/package.json` | SemVer |
| `voice-client` | `apps/voice-client/pubspec.yaml` | SemVer + build number（`x.y.z+N`） |

仓库顶层的 git tag（`v*.*.*`）由 [.github/workflows/release.yml](./.github/workflows/release.yml) 触发，**包含**三个 app 的最新 artifact；tag 自身的 SemVer 由 maintainer 在 PR 中同步三个 app version 后 bump。

发版流程详见 [CONTRIBUTING.md § 8](./CONTRIBUTING.md) 与 [README.md § 发布到 GitHub Release](./README.md#发布到-github-release)。

---

## 下一步

- 装环境 → [INSTALLATION.md](./INSTALLATION.md)
- 跑起来 → [USAGE.md](./USAGE.md)
- 改代码前 → [CONTRIBUTING.md](./CONTRIBUTING.md)
- 理解设计 → [ARCHITECTURE.md](./ARCHITECTURE.md)
- 运行时细节 → [openwiki/](./openwiki/)
- 历史 commit → `git log --oneline`
