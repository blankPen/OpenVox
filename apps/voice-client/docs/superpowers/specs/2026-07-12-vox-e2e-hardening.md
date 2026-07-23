# Vox E2E Hardening — Design Spec

**Date**: 2026-07-12
**Status**: Approved (user delegated all design decisions via `/goal`)
**Scope**: Cross-repo (Flutter client + openvox server + e2e test harness)

## Goal

5 项验收目标（用户原始诉求）：

1. Welcome → CTA → 通话页，无 -4010，server log 验证连接成功
2. 服务端日志能收到打招呼消息，客户端实时收到音频 + 气泡
3. 文字消息能传到 server log，AI 回复日志 + 双方气泡
4. 禁音、闭麦真实有效
5. 挂断退回 Welcome，server log 收到退出日志

约束：
- 100% 通过，零 ERROR（豁免清单之外）
- 不许拦截吞错，要根因修复
- 自行决定所有方案选择

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  iPhone 17 Simulator (Vox Flutter app)                                 │
│   welcome_screen ─tap CTA─► AgentScreen                                │
│     _Stage / _ChatPanel (frosted glass) / _ControlBar                 │
│       ↑                                                                │
│       components.MediaDeviceContextBuilder (NEW)                       │
│         enable/disableMicrophone / setSpeakerphoneOn                   │
│                                                                        │
│   lib/util/client_log.dart (NEW)                                       │
│     [Client] <tag> <message>  →  flutter log + OS log                  │
└────────────────────────────────────────────────────────────────────────┘
                │
                ▼ LiveKit SDK (2.6.1)
                ▼
         wss://livekit.openz.top:7443
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  OpenVox worker (openvox/main.py)                                       │
│  ── 已有三段 monkey-patch（保留）                                        │
│  ── NEW: [Worker]/[LLM-TEXT]/[Track] 结构化日志                          │
│  ── 修复 STT/TTS CancelledError → "never retrieved" 根因               │
│  ── 升级 livekit-agents 减少 data channel / signal client lifecycle 噪音│
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│  e2e/run_e2e_ui.py (主控)                                               │
│   12 phases（含新增 audio_reception 和 final_assertions）                │
│   WorkerLogTail: 增量读 /tmp/livekit-worker.log                         │
│   ParallelAudioSubscriber: spawn Python LiveKit 客户端订阅 agent 音频  │
│   FinalAssertions: 扫 ERROR 行 - 豁免命中 == 0                          │
└────────────────────────────────────────────────────────────────────────┘
```

## Server-Side Changes (openvox)

### A. 新增结构化日志标记

| Marker | 触发位置 | 用途 |
|---|---|---|
| `[Worker] 收到任务，正在加入房间: <room>` | 已有 `entrypoint()` | 派单验证 |
| `[Worker] 加入房间完成: <room>` | NEW: room connected callback | join 真成功 |
| `[Worker] 退出房间: <room> reason=<...>` | NEW: session close callback | hangup 验证 |
| `[Agent] 主动打招呼` | 已有 `on_enter()` | 招呼触发 |
| `[LLM-TEXT] <text>` | NEW: monkey-patch openai.LLM stream | 真实回复文本 |
| `[文本] 收到客户端消息: <text>` | 已有 | text channel 收到 |
| `[文本] 已将消息发送给 agent 触发回复` | 已有 | generate_reply 已 dispatch |
| `[Track] local mic <action> by <identity>` | NEW: track pub/unpub handler | mic mute 真生效 |
| `[Track] remote audio <action> from <identity>` | NEW: track sub handler | 音频真到客户端 |

### B. 根因修复（非吞错）

**B1. STT/TTS `_GatheringFuture exception was never retrieved`**

根因：disconnect → parent cancel 子进程所有 task → 子进程 gather 被 cancel → inner recv_task 抛 CancelledError 但没人 await → asyncio 兜底打 ERROR。

修复：在 STT 和 TTS 的 `_run` 里主动 drain inner task，确保 exception 被 retrieve：

```python
async def _fixed_run(self):
    recv_task = asyncio.create_task(self._inner_recv())
    try:
        await recv_task
    except asyncio.CancelledError:
        recv_task.cancel()
        try:
            await recv_task
        except (asyncio.CancelledError, Exception):
            pass
        raise
```

**B2. `publisher data channel '...' closed unexpectedly`**

根因：LiveKit Rust client SDK 把"server 主动 close"误判为 unexpected 并打 ERROR。
修复：升级 `livekit-agents` 到最新稳定版（包含 upstream fix）。如果升级后仍出现，加豁免 + issue link。

**B3. `dropping pass-through signal — no stream available`**

同 B2，依赖升级。

### C. 既有 patch 保留
- STT `CancelledError` 静默退出 patch（已有）
- `_cli_log.setup_logging` no-op（已有）
- openai SDK Hermes usage-only chunk filter（已有）

## Client-Side Changes (agent-starter-flutter)

### D. agent_screen.dart: mic/speaker 接通 SDK

用 `components.MediaDeviceContextBuilder` 包裹 `_ControlBar`：

```dart
components.MediaDeviceContextBuilder(
  builder: (ctx, roomCtx, mediaDeviceCtx) => _ControlBar(
    micOn: mediaDeviceCtx.microphoneOpened,
    speakerOn: mediaDeviceCtx.isSpeakerOn ?? true,
    onMicToggle: () => mediaDeviceCtx.microphoneOpened
        ? mediaDeviceCtx.disableMicrophone()
        : mediaDeviceCtx.enableMicrophone(),
    onSpeakerToggle: () => mediaDeviceCtx.setSpeakerphoneOn(
        !(mediaDeviceCtx.isSpeakerOn ?? true)),
    ...
  ),
)
```

### E. lib/util/client_log.dart (NEW)

```dart
import 'dart:developer' as developer;
class ClientLog {
  static void event(String tag, String message) {
    debugPrint('[Client] $tag $message');
    developer.log('[$tag] $message', name: 'vox.client');
  }
  static void audioTick(int frames, String from) {
    debugPrint('[Client] audio recv frames=$frames from=$from');
  }
}
```

### F. 客户端埋点

| 事件 | 位置 | 日志 |
|---|---|---|
| connect start | `AppCtrl.connect()` | `[Client] connect start` |
| connect success | `_handleSessionChange(connected)` | `[Client] connect success room=<name>` |
| disconnect | `_handleSessionChange(disconnected)` | `[Client] disconnect reason=<...>` |
| mic toggle | `_ControlBar.onMicToggle` | `[Client] mic <enabled\|disabled>` |
| speaker toggle | `_ControlBar.onSpeakerToggle` | `[Client] speaker <on\|off>` |
| text send | `_InputBar` submit | `[Client] text send: <text>` |
| text recv | `session.messageStream` listener | `[Client] text recv from=<agent>: <text>` |
| audio recv | `session` remote audio track subscription | `[Client] audio recv frames=N from=<agent>` |
| hangup | `AppCtrl.disconnect()` | `[Client] hangup` |
| chat toggle | `_chatOn` setState | `[Client] chat <open\|close>` |

## E2E Test Changes

### G. e2e/run_e2e_ui.py 重构

12 phases:
1. setup
2. launch
3. welcome
4. theme
5. start_call（追加并行 subscriber 启动）
6. mic_toggle
7. speaker_toggle（新增）
8. chat_open
9. greeting_audio（新增，subscriber WAV 断言）
10. text_round
11. hangup
12. final_assertions（新增，ERROR 严格扫）

### H. WorkerLogTail helper（脚本内）

```python
class WorkerLogTail:
    """增量读 /tmp/livekit-worker.log"""
    def __init__(self, path): ...
    def wait_for(self, marker, timeout) -> str: ...
    def count_errors_since(self, since_offset, exempt_patterns) -> int: ...
    def snapshot_offset(self) -> int: ...
```

### I. e2e/parallel_audio_subscriber.py (NEW)

- 用 `livekit` Python SDK 以 second participant 加入 room
- 订阅 agent audio track
- 录 5s PCM 落 `e2e/audio/greeting-<ts>.wav`
- 通过 env vars：`E2E_ROOM_NAME`、`E2E_AUDIO_OUT`、`E2E_RECORD_DURATION_SEC`
- 用现有 `tests/e2e_pipeline.py` 的 `_gen_token` / `_save_wav` 逻辑

### J. WAV 断言

```python
def assert_wav_non_silent(path, min_duration_s=0.3, min_max_amplitude=200):
    """16-bit PCM: 时长 ≥ 阈值 且 最大振幅 ≥ 阈值"""
    ...
```

### K. ERROR 豁免清单

```python
EXEMPT_ERROR_PATTERNS = [
    (re.compile(r"publisher data channel '_(reliable|lossy|data_track)' closed unexpectedly"),
     "livekit/rust-sdks issue — clean shutdown noise"),
    (re.compile(r"dropping pass-through signal — no stream available"),
     "livekit/rust-sdks issue — same as above"),
]
```

升级 livekit-agents 后逐条验证能否删除。

## Acceptance Criteria

| # | 目标 | 验证信号 |
|---|---|---|
| 1 | Welcome → CTA → agent 屏，无 -4010 | `[Worker] 加入房间完成` + mic 桃色像素 + 0 个 -4010 banner |
| 2 | Greeting 三处一致 | `[Agent] 主动打招呼` + `[LLM-TEXT]` + `tts end`；greeting WAV 非零；agent 文字气泡像素 |
| 3 | 文本消息三处一致 | `[文本] 收到` + `[LLM-TEXT]` + `tts end`；双方气泡像素；reply WAV 非零 |
| 4 | mic/speaker 真接通 | mic: `[Track] local mic unpublished/published` + `[Client] mic disabled/enabled`；speaker: `[Client] speaker off/on` + UI 像素 |
| 5 | Hangup → welcome | `[Worker] 退出房间` + CTA 桃色像素 |

## Out of Scope
- orb 状态机接真实 agent state（保留 4s 循环 demo）
- CJK 文字输入（idb ui text 不支持，仍用 ASCII）
- TTS 静音桩（配额已恢复，不再需要）
- 上游 LiveKit Rust SDK 源码修改（依赖 issue 跟踪）

## Risks
- 升级 livekit-agents 可能引入新兼容性问题；保留 venv 快照便于回退
- subscriber 进程与主测时间窗可能错位；用 room name + start_offset 严格同步
- mic 真 mute 后 LiveKit 不再 publish，worker log marker 必须在 unpublish 真正触发后才出；超时设为 5s 足够