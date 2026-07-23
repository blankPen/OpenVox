# OpenVox

> 实时语音 Agent 平台。客户端 (Flutter) + 后端 (Python LiveKit Worker)，通过 LiveKit Server 实时双向音频。

---

## 项目命名

- 项目名：**OpenVox**
- 核心词：**Vox** = voice / 声音
- 旧名：**openvox**（仅在新仓库尚未建立时短暂使用过，本次重构直接沿用 OpenVox 作为正式名）

---

## 架构

```
┌────────────────────────┐         ┌────────────────────────┐
│ Flutter 客户端          │ ──音频─→│ LiveKit Server (Docker)  │
│  apps/voice-client/    │ ←─音频──│  infra/docker-compose   │
│  (iOS/Android/Web/Mac) │         └────────────┬───────────┘
└────────────────────────┘                      │
                                                 ↓
                                        ┌────────────────────────┐
                                        │ Volcengine 语音 Worker  │
                                        │  apps/voice-agent/     │
                                        │  STT ⇨ LLM ⇨ TTS        │
                                        └────────────────────────┘
                                                       │
                                                       ↓
                                              ┌────────────────┐
                                              │ Hermes api_    │
                                              │ server (本地)  │
                                              └────────────────┘
```

- **apps/voice-agent/**：Python LiveKit worker，把音频流水线接起来
- **apps/voice-client/**：Flutter 客户端，用户界面
- **shared/**：两端都要遵守的契约（room 命名 / agent 协议 / token 字段）
- **infra/**：LiveKit Server 的本地部署
- **tooling/**：跨端编排脚本（Taskfile、dev-up / dev-down）

---

## 快速上手

```bash
# 1. 起 LiveKit Server（如果你没有现成的）
task dev:infra

# 2. 起 agent worker（在第二个终端）
cd apps/voice-agent
python main.py start
# 看到 "registered worker" 即就绪

# 3. 起 client（在第三个终端）
cd apps/voice-client
flutter run
```

> 三终端是为了让每端日志独立可看。一键起整套：见 `tooling/scripts/dev-up.sh`。

---

## 跨端契约

涉及两端都要看的"协议 / 命名 / 字段"，统一放在 [shared/](./shared/)。改这些文件**必须两个 app 都有人 review**。

---

## 目录

```
openvox/
├── apps/
│   ├── voice-agent/      # Python LiveKit worker
│   └── voice-client/     # Flutter 客户端
├── shared/               # 跨端契约（markdown + JSON example）
├── infra/                # LiveKit Server 本地部署
├── tooling/
│   ├── Taskfile.yaml     # 主编排
│   └── scripts/          # shell 脚本
├── .gitignore
└── README.md (本文件)
```

---

## 状态

骨架刚建。当前还没把 `~/workspace/agent-starter-flutter/` 和原 openvox 备份里的代码搬过来——等这一步你确认骨架 OK 再迁。
