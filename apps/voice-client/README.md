# voice-client（OpenVox Flutter 客户端）

> OpenVox 的 Flutter 客户端。与 [`apps/voice-agent`](../voice-agent/) 的 LiveKit Worker 通过 LiveKit 房间通信（语音 / 转写 / 文本消息），不直接连 voice-agent 进程。

承接顶层 [README.md](../../README.md) / [USAGE.md § 4.A 本地端到端调试](../../USAGE.md) / [ARCHITECTURE.md § 4.1 voice-client](../../ARCHITECTURE.md)；本文件讲 voice-client **内部的实际项目结构、关键文件、命令、测试、已知坑**。

---

## 1. 与 LiveKit Agents Flutter starter 的关系

本项目**派生自** [LiveKit Agents Flutter starter](https://github.com/livekit-examples/agent-starter-flutter)（即 `livekit-examples/agent-starter-flutter`），但已**超出 starter 范围做了大量定制**：

| 维度 | starter 现状 | OpenVox 现状 |
|---|---|---|
| `pubspec.yaml` `name` | `voice_assistant` | **未改**（仍 `voice_assistant`；迁移到 `openvox_voice_client` 是 backlog） |
| `pubspec.yaml` `description` | "A sample AI Voice Assistant app" | **未改**（同上） |
| Android `package_name` | `com.livekit.example.VoiceAssistantFlutter` | **未改**（同上） |
| iOS `bundle_id` | `com.livekit.example.VoiceAssistant-flutter` | **未改**（同上） |
| `lib/` 子目录结构 | flat（lib/*.dart） | **已分 9 个子目录**（audio / logs / controllers / screens / support / ui / util / widgets + 顶层 app.dart / livekit_config.dart） |
| Token 签发 | 走 LiveKit Cloud Sandbox | **自签 HS256**（见 §4） |
| 端到端测试 | 无 | **3 套**：widget test / Patrol native integration test (`integration_test/`) / Python e2e harness (`e2e/`) |
| 资产 | 默认 LiveKit screenshot | 自带 `assets/terminal.png` 等 |
| 测试运行方式 | `flutter test` | 上述 3 套各自不同命令（§6） |

> **包名 / bundle ID / description 仍是 starter 默认值** —— 这是 OpenVox 改 starter 模板的**已知未竟事项**。改之前先 grep `com.livekit.example.VoiceAssistant` 看影响面（CI、Play Store 现有 listing 等）。

---

## 2. 项目结构

```
apps/voice-client/
├── lib/                          # Dart 源码
│   ├── main.dart                 # 入口：dotenv.load → runApp(VoiceAssistantApp)
│   ├── app.dart                  # MaterialApp + 路由 + Session 装配
│   ├── app_ctrl.dart             # 主 controller（已迁出本目录到 controllers/，根目录是兼容 alias）
│   ├── livekit_config.dart       # ★ LiveKit URL / room / agent_name / DEV-ONLY API key
│   ├── exts.dart                 # Dart extensions
│   ├── audio/                    # 音频采集 / 播放 / 静音逻辑
│   ├── logs/                     # 日志输出
│   ├── controllers/              # 业务 controllers（多个）
│   ├── screens/                  # 页面 widgets
│   ├── support/                  # 错误 / Loading / 空态等支持 widget
│   ├── ui/                       # 设计 token、theme、原子组件
│   ├── util/                     # 工具函数
│   └── widgets/                  # 复合 widget
├── test/                         # Dart unit / widget test
│   └── widget_test.dart          # 1 个 starter 默认 widget_test
├── integration_test/             # Patrol native integration test（主 e2e 驱动）
│   ├── smoke_test.dart
│   ├── test_bundle.dart
│   └── vox_e2e_test.dart         # 主要 e2e 套件
├── e2e/                          # Python + Patrol 旁路（详见 §6）
│   ├── README.md
│   ├── FUNCTIONAL_MATRIX.md
│   ├── PATROL_GUIDE.md
│   ├── e2e_test.py               # LiveKit pipeline 测试
│   ├── run_e2e_ui.py             # 9 阶段 UI walkthrough（idb）
│   ├── verify_flutter_app.py     # Twirp listener
│   ├── parallel_audio_subscriber.py
│   ├── helpers/                  # 共享 Python 工具
│   ├── workflows/                # GitHub Actions workflow 片段
│   ├── assets/                   # 静态资源
│   ├── logs/                     # 运行时日志
│   └── screenshots/              # UI walkthrough 截图
├── assets/
│   └── terminal.png              # bundle 到 app 的图片
├── android/                      # Android Gradle 工程
├── ios/                          # iOS Xcode 工程（CocoaPods）
├── macos/                        # macOS 工程
├── web/                          # Web 工程
├── .env.example                  # 仅含 LIVEKIT_SANDBOX_ID（占位）
├── .env                          # gitignored；本地从 .env.example 复制
├── pubspec.yaml                  # 包名 voice_assistant / version 1.0.0+14 / Flutter SDK ^3.5.1
└── README.md                     # 本文件
```

---

## 3. 关键依赖（pubspec.yaml）

| 包 | 版本 | 用途 |
|---|---|---|
| `livekit_client` | ^2.6.1 | LiveKit Flutter SDK（房间、track、participant） |
| `livekit_components` | ^1.3.0 | LiveKit UI 组件 |
| `dart_jsonwebtoken` | ^3.4.1 | **自签 HS256 token**（不走 LiveKit Cloud Sandbox） |
| `flutter_dotenv` | ^6.0.0 | 启动时 load `.env` |
| `provider` | ^6.1.2 | 状态管理 |
| `http` | ^1.3.0 | 自建 token server 对接 |
| `chat_bubbles` | ^1.6.0 | 文本消息 UI |
| `google_fonts` | ^6.3.3 | 中文字体（Noto Sans SC 等） |
| `shimmer` | ^3.0.0 | Loading skeleton |
| `patrol` (dev) | ^3.17.0 | native integration test CLI |

完整依赖见 [pubspec.yaml](./pubspec.yaml)。

---

## 4. 配置（两个层级）

### 4.1 `lib/livekit_config.dart`（编译期常量）

**这是 OpenVox 与 starter 最大的差异点**。Starter 走 LiveKit Cloud Sandbox，本项目**自签 HS256 token**，凭证硬编进二进制：

```dart
// lib/livekit_config.dart（节选）
const liveKitUrl       = 'wss://livekit.openz.top:7443';
const roomName         = String.fromEnvironment('VOX_E2E_ROOM_NAME',
                              defaultValue: 'openz-room');
const agentName        = 'openz';          // ⚠ 当前是 openz，不是 openvox
const liveKitApiKey    = 'openz';          // ⚠ DEV-ONLY（详见下文）
const liveKitApiSecret = '35b58a6...';     // ⚠ DEV-ONLY（详见下文）
const tokenTtlSeconds  = 60 * 60 * 24;     // 24h
```

**安全声明**（文件头注释原文翻译）：

> 文件里的 API key / secret 是 **DEV 凭证**，会**打进 client 二进制**发布。任何反编译 IPA / APK 的人都能拿到并**伪造** token。
> 这仅在**本地开发 + 私有 LiveKit Server**时能接受。
> **任何 laptop 之外的部署，都必须换成从你控制的 token server fetch 凭证**。

### 4.2 `.env`（运行时）

由 `flutter_dotenv` 在 `main()` 入口 load；当前只用一个字段：

```bash
# apps/voice-client/.env.example（提交到仓库的占位）
LIVEKIT_SANDBOX_ID=<your-sandbox-id>
```

> OpenVox 客户端**默认走自签 token**（见 §4.1），**不走** LiveKit Cloud Sandbox。`.env` 字段是为**未来**接 LiveKit Cloud 或自建 token server 预留的，目前**未消费**。

### 4.3 与 `apps/voice-agent` 的关系

| 客户端要的 | voice-agent 给的 | 配置位置 |
|---|---|---|
| LiveKit URL | LiveKit Server 地址 | 客户端硬编 + `~/.openvox/config.json` 的 `livekit.url`（worker 端） |
| `agent_name` | Worker `WorkerOptions(agent_name=...)` | 客户端硬编 `openz` ↔ worker `livekit.agent_name`（必须一致） |
| Room | 任意；派单表路由 | 客户端硬编 `openz-room` + `lk dispatch create --room <同>` |
| Token | Self-signed HS256 with DEV key | 客户端 `dart_jsonwebtoken` |

> **`agent_name` 必须保持 `openz`（当前值）**：LiveKit 派单表按此注册；改之前要先看 [`apps/voice-agent/CLAUDE.md` § 已知坑](../voice-agent/CLAUDE.md) 的说明。OpenVox 计划改名到 `openvox`，等外部 app 迁移后再改。

---

## 5. 开发命令

### 5.1 跑起来

```bash
# (1) 装 Flutter 依赖（首次或 .dart_tool 漂移时）
flutter pub get

# (2) 拷贝 .env（如不存在）
[[ -f .env ]] || cp .env.example .env

# (3) 起 LiveKit Server + voice-agent worker（另两个终端 / 一个 task dev:up）
(cd ../../infra && docker compose up -d)
(cd ../voice-agent && python main.py start)

# (4) 派单 + 客户端进房
lk dispatch create --dev --room openz-room --agent-name openz

# (5) 起 Flutter（自动选已连设备）
flutter run                              # 选已连 iPhone / Android
flutter run -d <device-id>               # 指定设备
flutter run -d "iPhone 17"               # iOS Simulator
flutter run -d chrome                    # Web
```

### 5.2 出包

```bash
# Android debug APK（最快）
flutter build apk --debug
# → build/app/outputs/flutter-apk/app-debug.apk

# Android release APK（需要 keystore；见 CONTRIBUTING.md § 8.2）
flutter build apk --release

# iOS simulator（无需 codesign）
flutter build ios --debug --no-codesign --simulator
# → build/ios/iphonesimulator/Runner.app

# iOS device（需要 Xcode + provisioning profile）
flutter build ios --release

# 推荐：用顶层 wrapper 脚本（同时校验 + 拼装正确 flag）
../../tooling/scripts/build-client.sh android
../../tooling/scripts/build-client.sh ios
```

> **iOS CocoaPods 已知问题**（详见 `tooling/scripts/build-client.sh` 注释）：Flutter 3.44 的部分 SwiftPM 迁移会让 Flutter.framework unlink。当前 Flutter 项目**仍依赖 CocoaPods-only 插件**，所以脚本会显式跑 `flutter config --no-enable-swift-package-manager`，等所有插件支持 SwiftPM 再切。

### 5.3 跑测试

3 套测试入口（详见 §6）：

```bash
flutter test                                          # widget_test（最快）
patrol test --target integration_test/vox_e2e_test.dart   # native integration
(cd e2e && python3 run_e2e_ui.py)                     # UI walkthrough（需 iPhone Simulator + idb）
```

### 5.4 Lint / Analyze

```bash
flutter analyze                          # 静态分析（CI 也跑：flutter analyze --no-fatal-warnings --no-fatal-infos）
```

---

## 6. 测试（3 套互补）

详见 [`e2e/README.md`](./e2e/README.md) + [`e2e/PATROL_GUIDE.md`](./e2e/PATROL_GUIDE.md) + [`e2e/FUNCTIONAL_MATRIX.md`](./e2e/FUNCTIONAL_MATRIX.md) —— 本节只给入口。

| 套件 | 位置 | 驱动 | 用途 | 命令 |
|---|---|---|---|---|
| **widget test** | `test/widget_test.dart` | `flutter test` | 单元 / widget smoke | `flutter test` |
| **integration_test（Patrol）** | `integration_test/*.dart` | `patrol test` | 真机 / 模拟器 native integration | `patrol test --target integration_test/vox_e2e_test.dart` |
| **e2e UI walkthrough（Python）** | `e2e/run_e2e_ui.py` | `idb` | iOS Simulator UI 9 阶段 walkthrough，每步截图 + 断言 | `python3 e2e/run_e2e_ui.py` |
| **e2e LiveKit pipeline（Python）** | `e2e/e2e_test.py` | LiveKit Python SDK | Token 签发 / Twirp / 双客户端 publish-subscribe | `python3 e2e/e2e_test.py` |
| **Twirp listener** | `e2e/verify_flutter_app.py` | 与 Flutter app 同跑 | 验证客户端确实进了 `openz-room` | `python3 e2e/verify_flutter_app.py` |

`run_e2e_ui.py` 的 9 阶段：Setup → Launch → Welcome → Theme → Start call → Mic toggle → Chat panel → Text input + send → Hangup。详见 [e2e/README.md](./e2e/README.md)。

> **2026-07 更新**：Patrol-based 测试套件（`integration_test/`）已成为**主测试驱动**；原 `idb` 脚本（`run_e2e_ui.py`）保留为旁路 cross-check。

---

## 7. 已知坑（仅 OpenVox 相关）

> LiveKit Flutter SDK 自身的 issue 不在本表；查 [LiveKit Flutter SDK repo](https://github.com/livekit/client-sdk-flutter)。

| 症状 | 原因 | 处理 |
|---|---|---|
| `flutter build ios --debug` 报 `Flutter.framework unlinked` | Flutter 3.44 部分 SwiftPM 迁移与 CocoaPods-only 插件冲突 | `flutter config --no-enable-swift-package-manager`（脚本已自动跑） |
| 客户端连得上但 worker 不派单 | `agent_name` 与 LiveKit 派单表不一致 | 确认 `lib/livekit_config.dart` 的 `agentName` 与 `~/.openvox/config.json` 的 `livekit.agent_name` 一致（当前都是 `openz`） |
| Token 签发后被 LiveKit 拒（401） | DEV key/secret 跟 LiveKit Server 不匹配 | 确认 `lib/livekit_config.dart` 的 key/secret 与 `infra/docker-compose.yml` 起的 LiveKit Server 一致 |
| `idb ui text` 输入中文失败 | `idb` 用 HID keycodes，只支持 ASCII | e2e 用 ASCII 占位消息；中文验证靠 screenshot |
| iOS 出包报 `Provisioning profile ... doesn't include signing certificate` | 当前没配 Apple Developer 签名 | 本地用 `--no-codesign --simulator` 出；release 需配 signing（[CONTRIBUTING.md § 8](../../CONTRIBUTING.md)） |
| `flutter pub get` 卡住（Tsinghua mirror） | 网络问题 | `FLUTTER_STORAGE_BASE_URL=... PUB_HOSTED_URL=... flutter pub get` 或 `flutter --no-version-check pub get` |
| `gradle build` 报 Android SDK 缺失 | `local.properties` 没配 | 在 `android/local.properties` 写 `sdk.dir=/path/to/Android/sdk` |

---

## 8. CI 集成

`.github/workflows/ci.yml` 跑两个 job：

| Job | runner | 步骤 |
|---|---|---|
| `client-android` | `ubuntu-latest` | `flutter pub get` → materialize `.env` → `flutter analyze` → `flutter build apk --debug` → upload artifact |
| `client-ios` | `macos-latest` | `flutter pub get` → materialize `.env` → `flutter build ios --debug --no-codesign --simulator` |

完整配置见 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)。

---

## 9. 下一步

- 整体架构 / 与 voice-agent 的通信 → [ARCHITECTURE.md § 4.1](../../ARCHITECTURE.md)
- 跑通本地端到端 → [USAGE.md § 4.A 本地端到端调试](../../USAGE.md)
- 改代码前看 → [CONTRIBUTING.md](../../CONTRIBUTING.md)
- Flutter 客户端的 LiveKit 概念 → [LiveKit Flutter docs](https://docs.livekit.io/home/client-sdk/flutter/)
- 详细 e2e / Patrol 用法 → [e2e/PATROL_GUIDE.md](./e2e/PATROL_GUIDE.md)
- LiveKit 派单 → `lk dispatch create --dev --room openz-room --agent-name openz`（详见 [USAGE.md § 2.3](../../USAGE.md)）
