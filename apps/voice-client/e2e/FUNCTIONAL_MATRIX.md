# e2e 功能矩阵

> 基于 `lib/screens/{welcome,agent}_screen.dart` + `lib/widgets/control_bar.dart` 梳理

## 通用环境

| 项 | 值 |
| --- | --- |
| 模拟器 | iPhone 17 (UDID `31386DB9-7585-4AED-AC57-7CEEE70DD76B`) |
| Bundle ID | `com.livekit.example.VoiceAssistant-flutter` |
| LiveKit URL | `wss://livekit.openz.top:7443` |
| 房间 | `openz-room-{ts}-{rand}`（每次启动唯一） |
| Agent 名称 | `openz` |
| 后端 worker | `openvox-agent`（Volcengine STT） |
| 屏幕像素 | 1206×2622 (iPhone 17, @3x = 402×874 pt) |
| 状态栏 | 顶部约 100 pt |
| 安全区 | 顶部 + 底部 |

## 功能列表

### Phase 1：欢迎页（welcome_screen.dart）
- [ ] 1.1 顶部栏：左侧 "Vox" 文字 + 红色 live dot，右侧 36pt 圆形主题切换按钮
- [ ] 1.2 中部：VoxOrb（idle 状态，180pt，渐变粉色球体）
- [ ] 1.3 "Vox" 渐变文字 logo（V/o 是 fg 色，x 是 orb 渐变）
- [ ] 1.4 "just speak." 副标题
- [ ] 1.5 描述文字 "一句话,我就懂了。/实时语音 AI,陪你聊、帮你做、听你说。"
- [ ] 1.6 CTA 按钮 "开始语音通话 →"（粉橙渐变，圆角 9999px）
- [ ] 1.7 主题切换：tap 右上角月/日图标 → 切换 light/dark，背景色变化

### Phase 2：连接启动
- [ ] 2.1 tap CTA → 按钮变 loading（"正在连接…" + 旋转 spinner）
- [ ] 2.2 自动跳转 → agent 屏
- [ ] 2.3 真连接：worker 日志出现 "openvox-agent [Worker] 收到任务"
- [ ] 2.4 麦克风权限 -4010 不应再出现（已 simctl grant）

### Phase 3：Agent 屏（agent_screen.dart）
- [ ] 3.1 顶部栏：左侧返回按钮（chevron_left）+ 中间 "● 通话中 MM:SS" + 右侧主题切换
- [ ] 3.2 中部：VoxOrb（180pt，状态在 listening/thinking/speaking 间循环，每 4s 切换）
- [ ] 3.3 状态文字（"正在聆听" / "正在思考" / "正在回答"）+ 提示文字
- [ ] 3.4 底部控制栏 4 个按钮（麦克风/声音/聊天/挂断）
- [ ] 3.5 orb 状态自动循环（listening ↔ thinking ↔ speaking，4s 间隔）

### Phase 4：控制按钮（_ControlBar）
- [ ] 4.1 麦克风 tap：图标 mic ↔ mic_off，背景色 peach ↔ surface
- [ ] 4.2 声音 tap：图标 volume_up ↔ volume_off
- [ ] 4.3 聊天 tap：图标 chat_bubble ↔ chat_bubble_outline，背景色变化
- [ ] 4.4 挂断 tap：调 `appCtrl.disconnect()` → 跳回欢迎页

### Phase 5：Chat 面板（_ChatPanel）
- [ ] 5.1 tap 聊天 → frosted glass 面板从 opacity 0 淡入到 1（320ms）
- [ ] 5.2 面板 BackdropFilter blur(28, 28)
- [ ] 5.3 顶部 ShaderMask 渐隐 0→0.07 区域透明化
- [ ] 5.4 ChatScrollView 显示真实 LiveKit 消息（含时间戳）
- [ ] 5.5 _MessageBubble：你（粉色渐变右对齐）/ Vox（白色卡片左对齐）
- [ ] 5.6 _InputBar：圆角 22 输入框 + 圆形 send 按钮（粉橙渐变）
- [ ] 5.7 输入文字 → send 按钮高亮（alpha 1.0）
- [ ] 5.8 tap send → `appCtrl.sendMessage()` → `session.sendText()` → 后端 worker 处理

### Phase 6：消息流
- [ ] 6.1 真消息时间戳 20:30:00/20:30:12 等
- [ ] 6.2 user/agent 气泡区分
- [ ] 6.3 发送后端到端验证：worker 日志有 "openvox-agent" 活动

### Phase 7：清理
- [ ] 7.1 tap 挂断 → session.end() → 跳回 welcome
- [ ] 7.2 mic/speaker/chat 状态重置
- [ ] 7.3 worker 日志有 "closing agent session due to participant disconnect"
