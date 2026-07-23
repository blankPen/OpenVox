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

## Participant Metadata 字段（双方可读写）

存放在 `Participant.metadata`，JSON 字符串。约定：

```json
{
  "vox_version": "0.1.0",
  "client": "voice-client",
  "lang": "zh-CN",
  "prefers_tts_voice": "zh_female_shuangjing",
  "session_mode": "voice"
}
```

| 字段 | 类型 | 写 | 读 | 说明 |
|---|---|---|---|---|
| `vox_version` | string | 客户端 | 后端 | 跨端协议版本，未来 breaking change 时检查 |
| `client` | string | 客户端 | 后端 | 客户端 app 名，便于切分支 prompt |
| `lang` | string | 客户端 | 后端 | 主语言，影响 LLM 回复语言 |
| `prefers_tts_voice` | string | 客户端 | 后端 | TTS 偏好；后端 agent 启动时读并传给 STT/TTS |
| `session_mode` | enum: voice/text | 客户端 | 后端 | 当前会话模式 |

## 控制信令（DataChannel / RPC）

暂未启用，控制信令走 LiveKit RPC（agent → client）或 DataChannel（双向）。

待规划，不写 dead contract。

## TODO

- [ ] 确定 STT/TTS 模型切换的协议（按房间？按 participant？）
- [ ] 确定 LLM system prompt 注入位置（worker 内 vs 客户端 metadata）
- [ ] 错误码规范（客户端要区分"网络断开" vs "agent 未启动" vs "TTS 失败"）
