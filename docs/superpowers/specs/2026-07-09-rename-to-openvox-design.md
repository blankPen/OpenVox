# OpenVox 项目改名设计（2026-07-09）

## 背景

仓库当前在文档 / 代码 / 配置中仍使用 "openz" 作为项目名（`~/.openz/config.json`、`AGENT_NAME=openz`、`OPENZ_CONFIG` 环境变量等）。现已决定把项目名统一为 **OpenVox**。本 spec 列出所有需要改的位置、改不改、为什么。

## 决策（已与用户确认）

| # | 决策 | 决定 |
|---|---|---|
| 1 | 配置目录 `~/.openz/` → `~/.openvox/`，env `OPENZ_CONFIG` → `OPENVOX_CONFIG` | ✅ 改 |
| 2 | `AGENT_NAME` 从 `openz` 改为 `openvox`；`api_key` 保持 `openz`（远端 server 已注册此 key，改它要同步 server 配置） | ✅ AGENT_NAME 改，api_key 保留 |
| 3 | 项目目录 `/Users/pz/workspace/livekit` → `/Users/pz/workspace/openvox` | ✅ 改（用户手动 `mv`，commit 之后做） |
| 4 | `docs/superpowers/specs/`、`plans/` 历史文档中的 `~/.openz/` 引用 | ✅ 批量替换 |

## 改动清单

### A. 运行时配置层（必须改）

| 文件 | 改动 |
|---|---|
| `config.py` | 默认路径 `~/.openz/config.json` → `~/.openvox/config.json`；env 名 `OPENZ_CONFIG` → `OPENVOX_CONFIG`；模块头注释同步 |
| `main.py` | 头部注释路径；`_OPENCZ_USER_ID` → `_OPENVOX_USER_ID`（顺手修 C→V 拼写错误，2 处）；logger 名 `volcengine-agent` → `openvox-agent` |
| `scripts/start.sh` | `OPENZ_CONFIG` 引用；`$HOME/.openz/config.json` 路径；错误信息文案 |
| `scripts/start_bridge.sh` | 同上 |
| `scripts/bridge_server.py` | 注释里 `~/.openz/config.json` 路径 |
| `~/.openz/config.json`（用户机器上） | `livekit.agent_name: "openz"` → `"openvox"`；**注意**：`api_key` 保持 `"openz"` 不变 |

### B. 测试层

| 文件 | 改动 |
|---|---|
| `tests/test_config.py` | 函数名 `test_default_path_is_openz_config` 改 `test_default_path_is_openvox_config`；`Path("~/.openz/...")` → `Path("~/.openvox/...")`；monkeypatch 改 `OPENVOX_CONFIG`；docstring 路径注释 |
| `tests/test_main_build_session.py` | docstring 注释路径；fixture docstring 路径 |
| `tests/test_openai_llm_hermes_compat.py` | docstring 注释路径 |
| `tests/e2e_pipeline.py` | `AGENT_NAME` 默认值 `"openz"` → `"openvox"`（与 config.json 对齐） |
| `tests/e2e_realtime.py` | `AGENT_NAME` 默认值 `"volcengine-agent"` → `"openvox"`（与 config.json 对齐） |
| `tests/test_e2e_fs_tools.py` | 硬编码绝对路径 `/Users/pz/workspace/livekit/.venv/bin/python` → `/Users/pz/workspace/openvox/.venv/bin/python`（项目目录改名后必须改） |

### C. 文档与配置文件

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | `[project].name` `livekit-volcengine-worker` → `openvox`；description 改为 "OpenVox worker: Volcengine STT/TTS + Hermes OpenAI-compatible LLM" |
| `README.md` | 标题 `# LiveKit × Volcengine 语音 Agent` → `# OpenVox × Volcengine 语音 Agent`；首段加 OpenVox 介绍；`lk dispatch` 命令的 `--agent-name` 默认从 `volcengine-agent` 改为 `openvox` |
| `CLAUDE.md` | 文档标题与首段改为 OpenVox；`.env` 段关于 `AGENT_NAME=openz` 改为 `AGENT_NAME=openvox`；所有 `/Users/pz/workspace/livekit` 路径改为 `/Users/pz/workspace/openvox` |
| `docs/agent-capabilities-extension.md` | `/Users/pz/workspace/livekit` → `/Users/pz/workspace/openvox`；项目名段首 |
| `.claude-task-brief.md` | `/Users/pz/workspace/livekit` → `/Users/pz/workspace/openvox`；其他 `openz` 引用 |
| `docs/superpowers/specs/2026-07-04-agent-extensibility-design.md` | `~/.openz/` → `~/.openvox/`（3 处） |
| `docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md` | `~/.openz/` 引用与 `--agent-name openz` 命令 |
| `docs/superpowers/plans/2026-07-04-agent-extensibility-v0.1.md` | `~/.openz/` 引用 |
| `docs/superpowers/plans/2026-07-05-agent-filesystem-tools.md` | `--agent-name openz` 命令 |

### D. 不改（明确排除）

- `plugins/livekit-plugins-volcengine/`：上游 vendored 插件，目录名是 LiveKit 生态约定
- `wss://livekit.openz.top:7443`：实际生产 server 的 URL，DNS 不可改
- `livekit-plugins-volcengine`、`livekit-plugins-openai` 等 pip 包名：上游包
- `bridge_server.py` FastAPI `title="livekit-hermes-bridge"`：组件功能描述，不是项目名
- `volcengine.STT` / `volcengine.TTS` / `volcengine.RealtimeModel`：上游火山引擎插件类
- `.claude/worktrees/*`：gitignored 临时 worktree，会话级临时存在
- `livekit.yaml` 引用：当前仓库根没有这个文件（CLAUDE.md 描述已陈旧），无需动
- memory 文件 `/Users/pz/.claude/projects/.../memory/*.md`：用户自动记忆系统，不在仓库作用域

## 执行计划

1. **worktree 隔离**：已在 `worktree-rename-to-openvox` 分支（步骤 0 已完成）
2. **在 worktree 内**做 A + B + C 三类改动（先全文本替换，再做语义校对）
3. **验证**：
   - `grep -rIn "openz" .` （排除 `.venv`, `.git`, `.claude/worktrees`）— 预期 0 命中
   - `pytest tests/test_config.py tests/test_main_build_session.py tests/test_openai_llm_hermes_compat.py tests/test_volcengine_agent.py -v` — 全绿
   - 静态 smoke：`python -c "import config; print(config.CONFIG_PATH)"` — 打印 `/Users/pz/.openvox/config.json`
4. **commit + push + draft PR**：单 commit 标题 `refactor: rename project from openz to OpenVox`；body 列主要变更
5. **告诉用户**手工执行 `mv /Users/pz/workspace/livekit /Users/pz/workspace/openvox`（此动作必须在 merge 后做，否则 cwd 失效）
6. **merge 后**：让用户运行一次 `./scripts/start.sh` 烟雾测试，验证 `OPENVOX_CONFIG` 路径解析 + worker 注册到 `wss://livekit.openz.top:7443`（URL 不变）

## 风险

- **api_key 保持 "openz"**：与项目名不一致，但远端 server 已注册此 key；如果改名会导致 worker 签的 JWT 验不过（参考 CLAUDE.md "401 根因" 段）。文档里要显式记录这个例外。
- **目录改名破坏 cwd**：在 PR 合并到 main 之前不要动目录，否则其他 session 引用会断。
- **测试默认 `AGENT_NAME` 不一致**：e2e_realtime 写的是 `volcengine-agent`，e2e_pipeline 写的是 `openz` — 两者都与 config.json 不一致，全部统一为 `openvox`。
- **`_OPENCZ_USER_ID` 是 C→V 拼写错误**：在改名时一起修到 `_OPENVOX_USER_ID`，避免污染新名字。
