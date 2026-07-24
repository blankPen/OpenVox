# Files

- [跨端契约(shared/)](contracts.md) - Agent ↔ Flutter 客户端的命名约定、Participant Metadata 字段、Access Token Claims schema,定义在仓库根 shared/ 目录。
- [OpenVox 架构总览](overview.md) - 4 块架构图(Flutter 客户端 → LiveKit Server → voice-agent worker → Volcengine)、worker 生命周期、模块加载时安装的 monkey-patch。
- [OpenVox 会话拼装](session-wiring.md) - _build_session 如何组装 STT/LLM/TTS 流水线,VolcengineAgent 如何驱动逐房间对话;包括 on_enter 问候与文本输入回调。
