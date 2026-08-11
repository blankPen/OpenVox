# CONTRIBUTING — OpenVox

> 给 OpenVox 贡献代码 / 文档 / 脚本的完整规则。**先读 §1 黄金法则**，再按你的角色跳到对应章节。

---

## 1. 黄金法则（必读）

1. **改 [`shared/`](./shared/) 必须双 app review** —— room 命名、agent 协议、token claims、env 清单任何一处变更，都要在 PR 里勾选 `apps/voice-agent` **和** `apps/voice-client` 两个 reviewer。详见 [`shared/README.md` § 原则](./shared/README.md)。
2. **不要手改 `openwiki/**`** —— 它由 `.github/workflows/openwiki-update.yml`（`cron: 0 8 * * *`）每天从源码重新生成；想改运行时细节就去改源，手改 wiki 会被 CI 覆盖。详见 [CLAUDE.md](./CLAUDE.md) 与 [AGENTS.md](./AGENTS.md)。
3. **`pip install -e ./apps/voice-agent` 必须带 `--no-deps`** —— vendored Volcengine 插件钉了 `livekit-agents==1.5.4`；不带 `--no-deps` 会拆 `[otel, silero, turn-detector]` extras。详见 [`apps/voice-agent/CLAUDE.md` § 已知坑](./apps/voice-agent/CLAUDE.md)。
4. **`agent_name` 是 `openvox`**（v0.3.0+；v0.2.x 是 `openz`）—— LiveKit 派单表按 `openvox` 注册。详见 [`shared/room-naming.md`](./shared/room-naming.md) 与 [`apps/voice-client/lib/livekit_config.dart`](./apps/voice-client/lib/livekit_config.dart) 的 `agentName` 常量。
5. **不要把密钥 / token / 内部地址写进仓库** —— 仓库是公开的；本地配置走 `~/.openvox/config.json` 或环境变量。

---

## 2. 项目结构速查

```
openvox/
├── apps/
│   ├── agentd/           # Node + Fastify，ACP → OpenAI REST daemon（agentd CLI）
│   ├── voice-agent/      # Python LiveKit worker（openvox CLI）
│   └── voice-client/     # Flutter 客户端
├── shared/               # 跨端契约（markdown + JSON example）
├── infra/                # LiveKit Server 本地 Docker 部署
├── tooling/
│   ├── Taskfile.yaml     # 主编排（dev / build / install / release:check）
│   └── scripts/          # dev-up / dev-down / build-cli / install-cli / build-client
├── scripts/              # 顶层一键脚本：install.sh + install.ps1（用户入口）
├── .github/workflows/    # ci.yml（PR 冒烟）+ release.yml（tag → GitHub Release）+ openwiki-update.yml
├── openwiki/             # CI 自动维护的 wiki（禁止手改）
└── 顶层 .md: README / INSTALLATION / USAGE / CONTRIBUTING / ARCHITECTURE
```

架构细节看 [ARCHITECTURE.md](./ARCHITECTURE.md)；运行时细节 / 配置字段全表 / 已知坑看 [openwiki/](./openwiki/)。

---

## 3. 开发环境

按 [INSTALLATION.md](./INSTALLATION.md) § 2 一键安装，或手动按 § 3 一步步来。

不要跳过：

- `python -m venv .venv`（避免污染系统 site-packages）
- `pip install -e ./apps/voice-agent --no-deps`（黄金法则 §3）
- `flutter pub get` + `cp .env.example .env`（CI 也走这一步）
- `(cd infra && docker compose up -d)`（LiveKit Server 本地 dev）

---

## 4. 分支命名

按目的选前缀：

| 前缀 | 用途 | 例 |
|---|---|---|
| `feature/<scope>` | 新功能 / 新能力 | `feature/agentd-claude-provider` |
| `fix/<scope>` | bug fix | `fix/voice-client-ios-codesign` |
| `release/<version>` | 发版准备（bump 版本号 / changelog） | `release/0.2.0` |
| `chore/<scope>` | 工具 / CI / 文档 / 重构（非功能性） | `chore/openwiki-template-refresh` |
| `worktree-<name>` | 长期 worktree（agent / 实验） | `worktree-agent-af0af9840fe25fdb0` |

`<scope>` 用小写连字符；保持 ≤ 32 字符；用名词而不是动词。

> 历史上 `selectable-agent-runtime-v2` 这种无前缀分支是早期实验，**不再接受新分支**用这种格式。

---

## 5. 提交约定（Conventional Commits）

主分支的 commit message 必须遵循：

```
<type>(<scope>): <subject>

<body>（可选）

<footer>（可选，BREAKING CHANGE / Closes #xx）
```

`<type>` 取值：

| type | 用途 | 例 |
|---|---|---|
| `feat` | 新功能 | `feat(cli): add filtered logs and resilient Hermes status` |
| `fix` | bug fix | `fix(release): build iOS simulator slice in a second invocation` |
| `perf` | 性能改进（无新功能） | `perf: cut user-perceived first-audio latency from 8.4s to ~0.6s` |
| `refactor` | 重构（既非 feat 也非 fix） | `refactor(voice-agent): split _build_session` |
| `test` | 只改测试 | `test(agentd): make CI-friendly for runners without claude CLI` |
| `docs` | 只改文档 | `docs(usage): add hermes setup walk-through` |
| `chore` | 工具 / CI / 依赖 / 构建 | `chore(ci): pin actions to valid release commits` |
| `style` | 格式化（不改逻辑） | `style(openvox-worker): black format` |
| `ci` | CI 配置 | `ci(agentd): cache pnpm store` |

`<scope>`（可选但推荐）取：`agentd` / `voice-agent` / `voice-client` / `release` / `ci` / `docs` / `tooling` / `shared`。

`<subject>` 用中文 / 英文均可；首字母不大写；末尾无句号；命令式语气（"add" 不是 "added"）。

**BREAKING CHANGE** 在 footer 单独一行：`BREAKING CHANGE: <description>`。

---

## 6. PR review 矩阵

提 PR 前自检：

| 你改了… | 必须勾选 reviewer | 额外 CI |
|---|---|---|
| `apps/voice-agent/**` | `@voice-agent-owners` | `voice-agent` job（ubuntu: test + wheel build） |
| `apps/voice-client/**`（含 lib / ios / android / web / macos） | `@voice-client-owners` | `client-android`（ubuntu）+ `client-ios`（macos） |
| `apps/agentd/**` | `@agentd-owners` | `agentd` job（typecheck + test + build） |
| `shared/**` | **双 app reviewer + 1 owner 总览** | 上面所有 app 的 CI |
| `infra/**` | `@infra-owners` | docker compose 配置 review |
| `tooling/Taskfile.yaml` / `tooling/scripts/**` | `@tooling-owners` | 本地至少跑一次 `task --list` + 受影响的 `task xxx` |
| `scripts/install.sh` / `scripts/install.ps1` | `@docs-owners` + `@tooling-owners` | macOS 跑 `bash -n`；Windows 跑 `pwsh -NoProfile -Command` |
| `.github/workflows/**` | `@ci-owners` | 至少在 fork 上 dry-run |
| `openwiki/**` | **禁止手改 PR** —— 改源 + 让 OpenWiki 自动刷新 | openwiki-update.yml |
| 顶层 `README.md` / `INSTALLATION.md` / `USAGE.md` / `CONTRIBUTING.md` / `ARCHITECTURE.md` | `@docs-owners` | [docs-check workflow](.github/workflows/docs-check.yml) |

> owner 名单见 GitHub `CODEOWNERS`（未来若未生成则由 maintainer 手动指派）。

PR 描述模板：

```markdown
## What
<一句话讲清楚改了什么>

## Why
<为什么要改；贴 issue / design doc 链接>

## How to verify
<复现 / 验证步骤；截屏或日志片段>

## Risks
<潜在影响；rollback 计划>

## Checklist
- [ ] 跑了相关 app 的测试
- [ ] 改了 `shared/` 时勾选了双 app reviewer
- [ ] 没把密钥 / token 写进仓库
- [ ] 不动 `openwiki/**`
```

---

## 7. 测试命令

按 app 分组：

### voice-agent（Python）

```bash
# 单元测试（无外部依赖，~46 个）
(cd apps/voice-agent && pytest tests/test_process_runtime.py \
                                       tests/test_llm_provider.py \
                                       tests/test_config.py \
                                       tests/test_hermes_runtime.py \
                                       tests/test_start_script_contract.py -v)

# e2e（需要 LiveKit + 火山引擎凭证；本地通常 skip）
(cd apps/voice-agent && ./scripts/run_tests.sh e2e)

# 全套（含 e2e）
(cd apps/voice-agent && ./scripts/run_tests.sh full)
```

### agentd（Node + vitest）

```bash
(cd apps/agentd && pnpm test)          # 单跑一次
(cd apps/agentd && pnpm test:watch)    # 监听模式
(cd apps/agentd && pnpm typecheck)     # tsc --noEmit
(cd apps/agentd && AGENTD_AUTO_START=1 ./scripts/verify.sh)   # 端到端验收
```

### voice-client（Flutter）

```bash
(cd apps/voice-client && flutter test)             # dart test
(cd apps/voice-client && flutter analyze)          # 静态分析
(cd apps/voice-client && flutter build apk --debug)   # Android 出包
(cd apps/voice-client && flutter build ios --debug --no-codesign --simulator)  # iOS 出包（macOS only）
```

### 全仓 lint / type-check

```bash
task lint                       # TODO: agent ruff + client flutter analyze（待补）
```

---

## 8. 发布流程

### 8.1 推 tag 触发 release.yml

```bash
# (1) 确认三个 app 的当前版本
task release:check
# agentd         0.1.0
# openvox        0.2.0
# voice-client   0.2.0+1

# (2) 在要发的 app 里 bump 版本号
#     agentd:    apps/agentd/package.json  → "version"
#     openvox:   apps/voice-agent/pyproject.toml → "version"
#     voice-client: apps/voice-client/pubspec.yaml → "version: x.y.z+N"

# (3) 推 tag（vMAJOR.MINOR.PATCH 格式）
git tag v0.2.0
git push origin v0.2.0

# (4) 看 GitHub Actions
#     .github/workflows/release.yml 跑 4 个 build job：
#       - agentd 三平台 matrix（linux / macos / windows）
#       - openvox 三平台 matrix（py3-none-any wheel + sdist）
#       - voice-client android APK（debug + release）
#       - voice-client iOS .app（simulator + device, 无 codesign）

# (5) release job 把所有 artifact 上传到 GitHub Release

gh release view v0.2.0
```

> 也可通过 Actions 页面的 "Run workflow" 手工触发并传入自定义 tag（如 `v0.2.0-rc1`）。

### 8.2 Android 签名（release APK）

仓库 Secrets 需要四个变量：

| Secret | 用途 |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `upload-keystore.jks` 的 base64 内容 |
| `ANDROID_KEYSTORE_PASSWORD` | keystore 密码 |
| `ANDROID_KEY_ALIAS` | key alias |
| `ANDROID_KEY_PASSWORD` | key 密码 |

未配置完整时，CI 仍会构建但产物命名为 `*-release-debug-signed.apk`（明确标识非生产签名）。

**首次生成 keystore（本地一次）**：

```bash
# (1) 生成 keystore
keytool -genkey -v \
  -keystore upload-keystore.jks \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -alias upload

# (2) 拷示例 key.properties 并编辑
cp apps/voice-client/android/key.properties.example \
   apps/voice-client/android/key.properties
# 编辑 key.properties：填 storePassword / keyPassword / keyAlias / storeFile

# (3) 把 keystore 编为 base64 存进 GitHub Secrets
#     macOS:
base64 -i upload-keystore.jks | pbcopy
#     Linux:
base64 -w 0 upload-keystore.jks

# (4) 把 keystore 文件 + key.properties 加入 .gitignore
#     （已经在 .gitignore 里，**不要 commit**）
```

> keystore 一旦丢失，Play Store 上现有用户无法升级到用新 keystore 签的版本 —— 必须用 Google Play App Signing 找回。**强烈建议把 keystore 也备份到 1Password / Bitwarden 等离线位置。**

### 8.3 Release 后验证

```bash
# 下载产物并 smoke test
gh release download v0.2.0

# 装 agentd（macOS）
tar -xzf agentd-0.2.0-macos.tgz
npm install -g ./package

# 装 openvox（wheel）
pipx install ./openvox-0.2.0-py3-none-any.whl

# 装 Flutter 客户端（Android）
adb install voice-client-0.2.0-android-release.apk
```

---

## 9. 如何新增文档 / 脚本

### 新增手写文档

按受众放位置：

| 受众 | 位置 |
|---|---|
| 全部用户 / 贡献者 | 顶层 `README.md` / `INSTALLATION.md` / `USAGE.md` / `CONTRIBUTING.md` / `ARCHITECTURE.md` |
| 单个 app 维护者 | `apps/<name>/README.md` 或 `apps/<name>/CLAUDE.md` |
| 跨端契约 | `shared/`（黄金法则 §1） |
| 单次实验 / RFC / spec | `apps/<name>/docs/superpowers/specs/<name>.md` |
| 自动生成（运行时细节） | 让 OpenWiki 生成；不要手写 `openwiki/` |

手写文档更新后，检查：

1. 链接是否还指向有效文件（用 `markdown-link-check`）
2. 锚点（如 `./README.md#section`）是否还有对应 H2/H3
3. 是否需要同步更新 `openwiki/INSTRUCTIONS.md` 的索引（仅当新增跨域概念时）

### 新增脚本

| 类型 | 位置 | 受众 |
|---|---|---|
| 用户入口一键 bootstrap | `scripts/` | 用户 |
| Release / build / install 工具 | `tooling/scripts/` | 维护者 / CI |
| 单 app 内部脚本 | `apps/<name>/scripts/` | 单 app 维护者 |
| e2e / 验收脚本 | `apps/<name>/scripts/verify.sh` 或 `e2e/` | CI |

写新脚本时遵守：

- `set -euo pipefail`（bash）/ `$ErrorActionPreference = "Stop"`（PowerShell）
- TTY-aware 颜色（`[[ -t 1 ]]` 判断）
- 幂等（重跑只补缺失项，不重建）
- `--help` / `Get-Help` 友好
- 失败时给出下一步建议（不是裸 exit 1）

---

## 10. 行为准则

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) v2.1。维护者有权删除违反准则的评论 / commits / PRs。

---

## 11. 提 PR 之前再过一遍

- [ ] 分支命名符合 §4
- [ ] Commit message 符合 §5
- [ ] 改 `shared/` 时双 app reviewer 已勾选（§6）
- [ ] 跑了相关 app 的测试（§7）
- [ ] 没把密钥 / token / 内部地址写进仓库（黄金法则 §5）
- [ ] 不动 `openwiki/**`（黄金法则 §2）
- [ ] 新增 / 改动文档 / 脚本符合 §9
- [ ] PR 描述按 §6 模板填写
- [ ] CI 全绿（本地 `task lint` 与 GitHub Actions 都过）

---

## 12. 下一步

- 装环境 → [INSTALLATION.md](./INSTALLATION.md)
- 跑起来 → [USAGE.md](./USAGE.md)
- 理解系统设计 → [ARCHITECTURE.md](./ARCHITECTURE.md)
- 找具体运行时细节 / 配置字段 / 已知坑 → [openwiki/](./openwiki/)
