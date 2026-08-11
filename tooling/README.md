# tooling

> 跨端编排层：把 voice-agent / voice-client / agentd 三个 app 的开发、构建、安装动作收成统一入口。

承接顶层 [README.md](../README.md) 与 [INSTALLATION.md](../INSTALLATION.md)；本文件讲 `tooling/` 内部的**Taskfile 与 shell 脚本如何分工**，以及何时用哪条命令。

---

## 1. 目录速查

```
tooling/
├── Taskfile.yaml         # 主编排（dev:* / build:* / install:cli / release:check）
├── README.md             # 本文件
└── scripts/
    ├── dev-up.sh         # 一键起 LiveKit + agent worker
    ├── dev-down.sh       # 停 LiveKit + agent worker
    ├── build-cli.sh      # 构建 agentd + openvox 两个 CLI
    ├── install-cli.sh    # 构建并全局安装两个 CLI
    ├── build-client.sh   # 构建 Flutter 客户端（android + ios）
    └── lib/
        ├── log.sh        # 共享日志 / TTY-aware 颜色 / die / have
        └── versions.sh   # 各 app 版本号提取工具
```

`tooling/scripts/lib/log.sh` 提供 `step / info / warn / err / die / have` 五个 shell 函数；所有 `*.sh` 脚本都 `. "$SCRIPT_DIR/lib/log.sh"` 共享一份。

---

## 2. Taskfile vs scripts/ 的分工

| 层 | 文件 | 角色 |
|---|---|---|
| **编排（Taskfile）** | `tooling/Taskfile.yaml` | 用 `go-task` 暴露的命令入口；可读性好（`task --list` 直接列出 + 描述） |
| **实现（shell 脚本）** | `tooling/scripts/*.sh` | 真正的命令体；可以被 Taskfile 调，也可以**独立**跑（CI、容器、手动） |

> **Taskfile 是薄 wrapper**：`task build:cli` → `{{.TOOLING}}/build-cli.sh all`，仅此而已。它存在的目的是 (a) 命名稳定 (`task build:cli` 不需要记脚本路径) (b) 可描述化 (`task --list` 列 18 条任务带 desc) (c) 可被 IDE / CI 直接调。

---

## 3. 何时用哪个

### 你应该用 `task ...`

- **在日常开发机上** —— 快速、命名稳定、可描述化
- 在 IDE / 编辑器里搜索命令时 —— `task --list` 一次看完所有可用动作
- 给新人讲"我们项目能跑什么" —— `task --list` 比 `./tooling/scripts/*.sh` 直观

### 你应该直接调 `./tooling/scripts/*.sh`

- **在 CI / GitHub Actions** —— `.github/workflows/ci.yml` 直接调 `bash -c '... {{.TOOLING}}/lib/versions.sh; app_version $app ...'`
- 在容器 / 镜像构建里（CI 没有 go-task 装） —— 直接 `bash tooling/scripts/build-cli.sh all`
- 写新 wrapper 脚本时 —— Taskfile 是 YAML，复杂判断写在 shell 里更清晰

> **互不冲突**：`task build:cli` 内部就是 `bash tooling/scripts/build-cli.sh all`。两者结果完全一致。

---

## 4. 完整命令清单

### dev:* —— 开发工作流

```bash
task dev:infra            # 起 LiveKit Server (Docker)
task dev:infra-down       # 停 LiveKit Server
task dev:agent            # 起 voice-agent worker (前台)
task dev:client           # 起 Flutter voice-client
task dev:up               # 一键起 LiveKit + agent（不含 client）
task dev:down             # 停 LiveKit
```

### build:* —— 本地构建

```bash
task build:cli            # 构建两个 CLI（agentd + openvox）
task build:cli:agentd     # 只构建 agentd（TypeScript → dist/）
task build:cli:openvox    # 只构建 openvox（Python wheel + sdist）
task build:client         # 构建 Flutter android + ios
task build:client:android # 只构建 Android APK
task build:client:ios     # 只构建 iOS .app
task build:all            # build:cli + build:client
```

### install:* —— 全局安装 CLI

```bash
task install:cli           # 构建 + 全局安装两个 CLI
task install:cli:agentd    # 只安装 agentd
task install:cli:openvox   # 只安装 openvox
```

> 全局安装细节（pipx / npm install -g / PATH 处理）见 [INSTALLATION.md § 3.3](../INSTALLATION.md) 与 `scripts/install-cli.sh` 头部注释。

### release:* —— 发版辅助

```bash
task release:check         # 输出每个 app 的当前版本（agentd / openvox / voice-client）
```

完整发布流程（推 tag → release.yml 4 job matrix → GitHub Release）见 [CONTRIBUTING.md § 8 发布流程](../CONTRIBUTING.md)。

### lint

```bash
task lint                  # 全量：agentd typecheck + voice-client flutter analyze
task lint:agentd           # 只跑 agentd 的 tsc --noEmit（pnpm typecheck）
task lint:client           # 只跑 voice-client 的 flutter analyze
```

`task lint` 实际跑的 3 步（与 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 对齐）：

1. `cd apps/agentd && pnpm typecheck` —— `tsc -p tsconfig.json --noEmit`
2. `cd apps/voice-client && flutter analyze --no-fatal-warnings --no-fatal-infos`
3. 提示信息：voice-agent 用 pytest 做结构 lint（详见 [CONTRIBUTING.md § 7](../CONTRIBUTING.md)）

> **前置依赖**：跑 `task lint:client` 前要先 `flutter pub get`（含 [`.env` 物化](../.github/workflows/ci.yml)）；跑 `task lint:agentd` 前要先 `pnpm install`。建议先 `task dev:up` 或 `./scripts/install.sh` 把环境装好再 lint。

### docs:check（链接验证）

```bash
task docs:check                # 全量：扫所有默认 .md，验内部链接 + GitHub-style 锚点
task docs:check:strict         # 同上（别名，保留向后兼容）
task docs:check:quiet          # 只打汇总，不打每个错误
```

底层是 [`tooling/scripts/check-doc-links.sh`](./scripts/check-doc-links.sh) —— bash 实现，无外部依赖，扫 `README.md` / `INSTALLATION.md` / `USAGE.md` / `CONTRIBUTING.md` / `ARCHITECTURE.md` / `CHANGELOG.md` + `tooling/` `infra/` `apps/*/README.md` `CLAUDE.md` 共 15 个文件：

- 提取所有相对路径形式内部链接（含可选 GitHub-style 锚点）
- 验证目标文件 / 目录存在（相对源文件）
- 验证锚点对应目标文件的 H2/H3 heading 存在（用 perl 实现 GitHub-style slug normalization，含 CJK）
- `--strict` 还报缺失的目标文件（默认会跳过，可能是占位）

> **何时跑**：改完任何 .md 后；改完任何文件名 / 目录结构后；CI 加 job 时；发版前。**当前未接到 CI**，建议未来在 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 加 `docs:check` job。

---

## 5. 如何新增 task 或 script

### 新增 `task xxx`（推荐）

编辑 `tooling/Taskfile.yaml`，在 `tasks:` 下追加：

```yaml
  my-new-task:
    desc: One-line description（出现在 task --list）
    cmds:
      - "{{.TOOLING}}/my-new-script.sh"
      # 或者直接写命令：
      # - echo "hello"
```

然后 `task --list` 验证。

### 新增 `tooling/scripts/my-new-script.sh`

模板：

```bash
#!/usr/bin/env bash
# my-new-script.sh — 简短一句话描述
#
# Usage:
#   ./tooling/scripts/my-new-script.sh [args]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/log.sh
. "$SCRIPT_DIR/lib/log.sh"

# 实现...
step "doing something"
info "done"
```

约定：

- `set -euo pipefail`（来自 [`tooling/scripts/lib/log.sh` 头部注释](./scripts/lib/log.sh) 的隐含约定）
- 用 `step / info / warn / err / die / have` 输出，不要 `echo`
- 支持 `--help` / `-h`，失败时给下一步建议（不是裸 `exit 1`）
- 幂等 —— 重跑只补缺失项
- TTY-aware 颜色（`log.sh` 已处理）

### 引用 `tooling/scripts/lib/log.sh`

```bash
# 路径相对当前脚本
. "$(dirname "$0")/lib/log.sh"
```

可用的函数（详见 [`lib/log.sh`](./scripts/lib/log.sh)）：

| 函数 | 用途 |
|---|---|
| `step <msg>` | 粗体青色步骤标题（开头 `==>`） |
| `info <msg>` | 绿色 ✓ 信息（成功路径） |
| `warn <msg>` | 黄色 ! 警告（写到 stderr） |
| `err  <msg>` | 红色 ✗ 错误（写到 stderr） |
| `die  <msg>` | err + exit 1 |
| `have <cmd>` | 静默 `command -v <cmd>` 检查 |

### 引用 `tooling/scripts/lib/versions.sh`

```bash
. "$(dirname "$0")/lib/versions.sh"
app_version agentd      # 输出 apps/agentd/package.json 的 version 字段
app_version openvox     # 输出 apps/voice-agent/pyproject.toml 的 version
app_version voice-client  # 输出 apps/voice-client/pubspec.yaml 的 version
```

`task release:check` 内部就用这三个函数。

---

## 6. 与顶层 `scripts/install.sh` / `scripts/install.ps1` 的关系

| 文件 | 受众 | 范围 |
|---|---|---|
| `scripts/install.sh` / `install.ps1` | **新用户** | 一键 bootstrap 整个开发环境（Python venv + openvox + Flutter + agentd + LiveKit） |
| `tooling/scripts/install-cli.sh` | **维护者 / CI** | 只把 CLI 构建并全局安装到 PATH（agentd via npm；openvox via pipx/pip） |
| `tooling/Taskfile.yaml` `install:cli*` | **日常开发** | wrapper，调 `install-cli.sh` |

**互不替代**：

- `scripts/install.sh` 是**首次**开发环境 bootstrap；不动 `~/.openvox/` / `~/.agentd/`，那些由 `openvox init` 写
- `tooling/scripts/install-cli.sh` 只装两个 CLI；要求环境里已经有 `.venv` 或可写 PATH
- `task install:cli` = `tooling/scripts/install-cli.sh`，仅命名稳定

详见 [INSTALLATION.md § 2 一键安装 vs § 3.3 装 openvox worker](../INSTALLATION.md)。

---

## 7. CI 集成

`.github/workflows/ci.yml` 直接调 shell 脚本（不走 Taskfile），原因：

- CI 镜像 `ubuntu-latest` 不预装 `go-task`；装它要 `brew install go-task` / `apt install go-task` / `go install`，多一层
- CI 需要的步骤是 `pnpm install && pnpm test && pnpm build`，直接写在 step 里比 `task build:cli:agentd` 更显式
- `release:check` 用 `bash -c 'set -e; ...'`，避免 Taskfile YAML escape

`.github/workflows/release.yml` 反过来用 `task release:check`（matrix 前先看版本号），因为本地 + CI 一致更好。

---

## 8. 调试 / 常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| `task: command not found` | 未装 go-task | `brew install go-task`（macOS）/ 见 [go-task.dev](https://go-task.dev/installation/) |
| `task build:cli` 报 `python not found` | 系统 PATH 不含 `python3` | 激活 `.venv` 或 `export PATH=$REPO_ROOT/.venv/bin:$PATH` |
| `task build:client:ios` 报 `requires macOS` | 当前是 Linux / Windows | iOS 只能在 macOS 构建 |
| `task --list` 显示的描述变了但跑出来还是旧行为 | Taskfile 改了但脚本没改 | Taskfile 是 wrapper；先看它调的是哪个脚本，再去改脚本 |

---

## 9. 下一步

- 整体项目结构看 [ARCHITECTURE.md § 2](../ARCHITECTURE.md)
- 发版流程（CI release.yml 怎么用 task release:check）看 [CONTRIBUTING.md § 8](../CONTRIBUTING.md)
- 用户视角的命令矩阵看 [USAGE.md § 2](../USAGE.md)
- 写新脚本的 5 条铁律看 [CONTRIBUTING.md § 9](../CONTRIBUTING.md)
