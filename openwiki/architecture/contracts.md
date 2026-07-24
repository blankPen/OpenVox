---
type: Reference
title: 跨端契约(shared/)
description: Agent ↔ Flutter 客户端的命名约定、Participant Metadata 字段、Access Token Claims schema,定义在仓库根 shared/ 目录。
tags: [contracts, shared, protocol, room-naming, claims]
---

# 跨端契约(`shared/`)

`shared/` 目录是 **agent worker** (`apps/voice-agent/`) 与 **Flutter 客户端** (`apps/voice-client/`) 之间的契约源头。三份文件分别钉死了命名、Participant Metadata 和 Token Claims:

- [`shared/agent-protocol.md`](../../shared/agent-protocol.md) — Participant Metadata 应用字段(`vox_version` / `client` / `lang` / `prefers_tts_voice` / `session_mode`)
- [`shared/room-naming.md`](../../shared/room-naming.md) — `agent_name` 与 `room_name` 命名规则
- [`shared/livekit-claims.example.json`](../../shared/livekit-claims.example.json) — Access Token 标准 Claims schema

`main.py` / `config.py` 当前**尚未消费**这些字段(`main._build_session` 只读 `livekit` / `volcengine` / `hermes` 三段)。改任意一端前先读这三份文件;不在 LiveKit 标准信令范围内的"应用层字段"以 `shared/agent-protocol.md` 为准。

## Agent 命名

`shared/room-naming.md` 钉了 `agent_name = "openvox"`。**但**当前 worker 实际注册名是 `openz`(由 `apps/voice-agent/config.json` 的 `livekit.agent_name` 决定,改不动):

- 历史原因:外部 app 仍在用 `lk dispatch create --agent-name openz` 派单,见 `apps/voice-agent/docs/superpowers/specs/2026-07-09-rename-to-openvox-design.md` §5 / §28 的"agent_name 保持 openz"决定。
- 操作要点:`lk dispatch create --agent-name` 必须等于 worker 实际注册的 `livekit.agent_name`,否则 LiveKit 不会把 job 派过去。详见 [Configuration → Config loader](../configuration/config-loader.md) 里 `livekit.agent_name` 那一节。

## Room 命名

`shared/room-naming.md` 给出的形式是 `[namespace-]{subject}-{short_id}`,全小写,禁纯数字 / `:` / `@` / `/` / 空格 / 中文。约定值:

| 用途 | Room 名模式 | 谁创建 |
|------|------------|--------|
| 本地开发 / e2e | `dev-{user}-{yyyyMMdd}` 例如 `dev-pz-20260723` | 客户端 |
| 生产(按租户) | `voice-{tenant_id}-{uuid}` | 派单服务 |
| 演示 | `demo` | 客户端 |

## Participant Metadata 应用字段

`shared/agent-protocol.md` 钉的 JSON 字段(存在 `Participant.metadata`,双方可读写):

| 字段 | 类型 | 写方 | 读方 | 用途 |
|------|------|------|------|------|
| `vox_version` | string | 客户端 | 后端 | 跨端协议版本,breaking change 时校验 |
| `client` | string | 客户端 | 后端 | 客户端 app 名,便于切分支 prompt |
| `lang` | string | 客户端 | 后端 | 主语言,影响 LLM 回复语言 |
| `prefers_tts_voice` | string | 客户端 | 后端 | TTS 偏好,后端 agent 启动时读 |
| `session_mode` | enum: voice / text | 客户端 | 后端 | 当前会话模式 |

`main.py` **当前没有实现**读这些字段。改了 agent 启动逻辑、读 `Participant.metadata` 时请先确认 `shared/agent-protocol.md` 的字段已稳定。

## Access Token Claims

`shared/livekit-claims.example.json` 给出生产 token 服务应 mint 的 JWT Claims 结构(`iss` / `sub` / `exp` / `videoGrant.{room,roomJoin,canPublish,canSubscribe,canPublishData}` / `metadata` / `sip` / `roomPreset`),以及一个 `dev-pz-20260723` 房间的样例 token。

`scripts/start.sh` 与 `tests/e2e_pipeline.py` 走的是 dev 路径,通过 `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` 直连,不需要按这份 Claims schema 单独 mint JWT。

## 修改时机

修改这三份文件前先确认改动两端的兼容性:

1. `shared/agent-protocol.md` 新增字段 → 客户端必须写入新字段 + 后端 `main.py` 必须读取,否则字段是死契约。
2. `shared/room-naming.md` 修改命名规则 → 客户端创建房间的代码必须同步改。
3. `shared/livekit-claims.example.json` 修改 Claims 字段 → token 服务与客户端解码逻辑必须同步改。

## Source anchors

- `shared/agent-protocol.md` (3 层结构 + Metadata 字段表 + TODO)
- `shared/room-naming.md` (Agent 命名 + Room 命名规则 + 派单方式)
- `shared/livekit-claims.example.json` (Claims schema + dev 房间样例)
- `shared/livekit-env.example.env` (本地 LiveKit 环境变量样例,worker 不直接消费但 e2e 测试会 source)
- `apps/voice-agent/docs/superpowers/specs/2026-07-09-rename-to-openvox-design.md` §5 / §28 ("agent_name 保持 openz" 的决定)
- `apps/voice-agent/CLAUDE.md` §已知坑 `livekit.agent_name` 那一条