# Agent ↔ Client 通信协议

> agent (Python worker) 与 client (Flutter app) 之间的元数据和控制信令字段定义。
> 不在 LiveKit 默认信令范围里的"应用层字段"放这里。

## 三层结构

```
┌────────────────────────────────────────────────────┐
│ Layer 3  应用字段 (本文档)                          │  ← 你正在看
├────────────────────────────────────────────────────┤
│ Layer 2  LiveKit Participant Metadata              │  ← LiveKit 标准字段
├────────────────────────────────────────────────────┤
│ Layer 1  LiveKit Access Token Claims               │  见 livekit-claims.example.json
└────────────────────────────────────────────────────┘
```

## 当前状态

OpenVox 当前**未启用**应用层字段。`Participant.metadata`（Layer 2）和 DataChannel / LiveKit RPC（控制信令）两端都没有读写代码，列在 TODO 里等后续版本落地。具体字段定义见 git 历史（重构前曾约定 `vox_version` / `client` / `lang` / `prefers_tts_voice` / `session_mode`）。

> 写入 `shared/agent-protocol.md` 的字段必须同时满足：(a) 客户端写入，(b) `main.py` 读取消费。**只写文档不接代码 = 死契约**，会被下一个看到这份文档的人误以为是已实现的协议。

## TODO

- [ ] 决定第一批上线的应用层字段（建议从 `lang` + `prefers_tts_voice` 起步）
- [ ] 写客户端写入 + `main.py` 读取代码（带测试锁）
- [ ] 决定 STT/TTS 模型切换的协议（按房间？按 participant？）
- [ ] 决定 LLM system prompt 注入位置（worker 内 vs 客户端 metadata）
- [ ] 错误码规范（客户端要区分"网络断开" vs "agent 未启动" vs "TTS 失败"）
- [ ] 控制信令走 LiveKit RPC（agent → client）还是 DataChannel（双向）
