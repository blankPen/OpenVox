# Shared — 跨端契约

本目录收录 `apps/voice-agent` (Python) 与 `apps/voice-client` (Flutter) 之间**两端都要遵守**的契约。

## 原则

- **只放 markdown / JSON / .env example**，不放任何 importable 代码。
- **依赖方向**：两端都可以引用本目录，反之绝不可以。
- **变更门槛**：任何对其中文件的修改，必须在 PR 里显式勾选两个 app 的 review。

## 清单

| 文件 | 用途 | 谁要读 |
|---|---|---|
| [room-naming.md](./room-naming.md) | LiveKit Room / Agent 命名规则 | 客户端 + 后端 |
| [agent-protocol.md](./agent-protocol.md) | agent ↔ client 通信字段（metadata、control messages） | 客户端 + 后端 |
| [livekit-claims.example.json](./livekit-claims.example.json) | Access Token claims 字段模板（生产 token 服务对接用） | 后端 / 自建 token 服务 |
| [livekit-env.example.env](./livekit-env.example.env) | 两端都要用的环境变量清单 | 客户端 + 后端 |

## 何时新增文件

> "两个端都要看/都要对接" → 放这里。
> "只有一个端用" → 放回那个端。

## 何时升级为 `packages/`

如果某天这里出现**真正可 import 的代码**（自动生成的 proto / Dart 类型 / Python 类型），再独立建 `packages/<name>/`，把代码搬过去。**当下克制**，不预判。
