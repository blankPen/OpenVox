# 删除 Hermes Bridge 中间层（2026-07-09）

## 背景

当前 LLM 链路：`main.py` (openai.LLM) → `scripts/bridge_server.py` (:8765) → Hermes api_server (:8642)。

bridge 引入时是为了做 LiveKit header → X-Hermes-Session-Id 的转译和额外 auth 隔离。实际情况：
- bridge 的 `_upstream_headers()` 透传 `x-livekit-room` / `x-livekit-user` / `x-livekit-agent`，但 `main.py` 根本没发 `X-Hermes-Session-Id`（task brief 明说"暂时不实现"）
- `bridge.livekit_room_name` 写死成 config 里的 `"demo"`，不是 `ctx.room.name`
- 也就是说 header 翻译**没被使用**
- bridge 自己的 auth (`BRIDGE_API_KEY`) 是独立一层，但用户已经能直接给 main.py 配 `hermes.api_key` 走 Hermes 自带的 auth

bridge 现在是个**空转的反向代理**。砍掉它，main.py 直接打 :8642，配置更干净，运维少一个进程。

## 决策（已与用户确认）

| # | 决策 | 决定 |
|---|---|---|
| 1 | `bridge` 段和 `bridge_server` 段怎么办 | 全部合并到 `hermes` 段；`bridge.livekit_room_name` 删了 |
| 2 | `main.py` 的 `extra_headers={"X-LiveKit-Room": ..., "X-LiveKit-User": ...}` | 删了（发出去是死路） |

## 改动清单

### A. 运行时配置层

| 文件 | 改动 |
|---|---|
| `main.py` | `bridge.{model,base_url,api_key}` → `hermes.{model,api_base,api_key}`；删 `extra_headers={...}`；删 `user_id`/`_OPENVOX_USER_ID` 整段（连同 `entrypoint()` 里等远端参与者的 future 逻辑） |
| `scripts/bridge_server.py` | **删除** |
| `scripts/start_bridge.sh` | **删除** |
| `~/.openvox/config.json` | 删 `bridge` 段 + `bridge_server` 段；`hermes` 段加 `"model": "hermes-agent"` |

### B. 测试层

| 文件 | 改动 |
|---|---|
| `tests/test_main_build_session.py` | `_make_fake_config` 改用 `hermes.*`；删 `livekit_room_name` 字段；删 `assert extra_headers["X-LiveKit-Room"] == "test-room"` 断言 |
| `tests/test_openai_llm_hermes_compat.py` | `_make_fake_config` 改用 `hermes.*` |

### C. 文档

| 文件 | 改动 |
|---|---|
| `CLAUDE.md` | 删 bridge 相关段（标题/链路描述里还有 bridge 字样的） |
| `README.md` | 删"启动 bridge"段；目录结构删 `scripts/bridge_server.py` / `start_bridge.sh`；首段 LLM 链路由"经 bridge 转发"改为"直连"；项目目录树里删 bridge 进程 |
| `pyproject.toml` | description 微调："Hermes OpenAI-compatible LLM via LiveKit Agents" 保持（无需改） |

## 不动

- `config.py` 模块（不动代码，只动文档字符串——`Config` 类对 schema 不敏感）
- `tests/test_config.py`（测 Config 类自身，与 schema 无关）
- `docs/superpowers/specs/2026-07-09-rename-to-openvox-design.md`（点对点记录改名那一刻的状态，bridge 当时还在）
- `.claude-task-brief.md`（历史 task brief）
- `tests/e2e_*.py`（用 os.environ 读 LIVEKIT_*，不依赖 bridge）

## 简化副作用

`entrypoint()` 删掉 user_id 等待逻辑后会少 ~20 行（future、participant_connected handler、connect 后查 remote_participants、wait_for、env 写回）。worker 进房后立刻 idle 等派单，不再"等 20s 没人就 return"。

## 执行计划

1. 写 spec（本文件）
2. 改 main.py + 测试 + 用户 config.json
3. `git rm scripts/bridge_server.py scripts/start_bridge.sh`
4. 改 CLAUDE.md + README.md
5. 验证：32 个单元测试全绿 + config 解析正确 + main.py import 干净
6. commit 推到 worktree-rename-to-openvox 分支
7. PR #1 自动更新

## 风险

- **没有任何 user → room 路由**：直连模式下，Hermes 看到的每个 LLM 请求是独立的，不会按房间分组。如果以后要恢复房间级 session 隔离，需要在 main.py 加 `X-Hermes-Session-Id: room:{name}:user:{id}` 头——这正是 bridge 原本该做但没做的事。
- **少一个 auth 隔离层**：以前 `BRIDGE_API_KEY="bridge"` 是 bridge 自己的 key，现在 main.py 直接用 `hermes.api_key`（`"livekit-bridge-test"`）。如果想换 LLM 后端，要直接换 config 里的 `hermes.api_base` + `api_key`。
