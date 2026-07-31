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
- **tooling/**：跨端编排脚本（Taskfile、dev-up / dev-down、build / install CLI）
- **apps/agentd/**：本地 ACP → OpenAI REST 转换守护进程，供 voice-agent 选择的 LLM 后端使用

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

## 本地打包与全局安装

全部由 `tooling/scripts/` 下的 shell 脚本封装。

### 构建 CLI

```bash
# 两个 CLI 都构建（agentd 编译到 apps/agentd/dist/，openvox 编译到 apps/voice-agent/dist/ 下的 wheel + sdist）
./tooling/scripts/build-cli.sh

# 只构建某一个
./tooling/scripts/build-cli.sh agentd
./tooling/scripts/build-cli.sh openvox
```

### 全局安装 CLI

```bash
# 一键构建并全局安装两个 CLI
./tooling/scripts/install-cli.sh

# 已经构建过的情况下，可以跳过重新构建
./tooling/scripts/install-cli.sh --no-build

# 单独安装某一个
./tooling/scripts/install-cli.sh agentd
./tooling/scripts/install-cli.sh openvox
```

安装后验证：

```bash
agentd --check            # 输出启动日志后退出（听不上可以随之 kill）
openvox --help            # 输出 openvox 的子命令列表
```

> `openvox` 会装到你的 venv / `--user` 环境 / `pipx` 中；拿到路径后可能要手动加到 PATH。
> `agentd` 会装到 `npm` / `pnpm` 的全局 bin 目录（macOS 常见 `/Users/<you>/.local/bin`）。

### 构建 Flutter 客户端

```bash
# Android APK（debug）与 iOS .app（simulator + device，无 codesign）
./tooling/scripts/build-client.sh

# 单独构建
./tooling/scripts/build-client.sh android
./tooling/scripts/build-client.sh ios
```

产物路径：
- Android: `apps/voice-client/build/app/outputs/flutter-apk/app-debug.apk`
- iOS Simulator: `apps/voice-client/build/ios/iphonesimulator/Runner.app`
- iOS Device: `apps/voice-client/build/ios/iphoneos/Runner.app`

### Taskfile 等价命令

```bash
task build:cli                       # 构建两个 CLI
task build:cli:agentd                # 只构建 agentd
task build:cli:openvox               # 只构建 openvox
task build:client                    # 构建 Flutter 客户端（android + ios）
task build:client:android            # 只构建 APK
task build:client:ios                # 只构建 iOS .app
task install:cli                     # 构建 + 全局安装两个 CLI
task install:cli:agentd              # 只安装 agentd
task install:cli:openvox             # 只安装 openvox
task release:check                   # 输出每个 app 的当前版本
```

---

## 发布到 GitHub Release

推送 `v*.*.*` 格式的 tag 即可触发 [.github/workflows/release.yml](.github/workflows/release.yml)：

1. `meta` job 推导 tag / version / 是否 prerelease
2. 四个 build job 并行跑（agentd 三平台 matrix、openvox 三平台 matrix、Android APK、iOS .app）
3. `release` job 把所有 artifact 上传到该 tag 的 GitHub Release，并打印安装指令

产物命名（假设 tag = v0.2.0）：

| 文件 | 内容 |
|---|---|
| `agentd-0.2.0-linux.tgz` / `macos.tgz` / `windows.tgz` | `npm pack` 产物，供 `npm install -g` / `pnpm add -g` 使用 |
| `openvox-0.2.0-py3-none-any.whl` 与 `openvox-0.2.0.tar.gz` | Python wheel + sdist |
| `voice-client-0.2.0-android-debug.apk` 与 `...-release.apk` | Android（debug + 正式签名 release） |
| `voice-client-0.2.0-ios-simulator.zip` 与 `...-ios-device.zip` | iOS Runner.app 打包 |

> 也可通过 Actions 页面的 "Run workflow" 手工触发并传入自定义 tag（如 `v0.2.0-rc1`）。
> 推送之前最好先看一眼 [.github/workflows/ci.yml](.github/workflows/ci.yml) 是否全绿。

Android 正式发布使用仓库 Secrets 中的 `ANDROID_KEYSTORE_BASE64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS` 和 `ANDROID_KEY_PASSWORD`。未配置完整时仍会使用开发密钥构建，但产物会明确命名为 `*-release-debug-signed.apk`，避免被误认为生产签名包。可在本地生成上传密钥：

```bash
keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
cp apps/voice-client/android/key.properties.example apps/voice-client/android/key.properties
# 编辑 key.properties 后，本地运行：
./tooling/scripts/build-client.sh android
```

将 keystore 编码后保存为 `ANDROID_KEYSTORE_BASE64`：macOS 使用 `base64 -i upload-keystore.jks | pbcopy`，Linux 使用 `base64 -w 0 upload-keystore.jks`。密钥文件和 `key.properties` 已被 git 忽略，不要提交到仓库。

同时还有 [.github/workflows/ci.yml](.github/workflows/ci.yml) 在每个 PR 上做冒烟构建（typecheck / test / wheel / analyze / APK / iOS .app），用来在合并前发现回归。


---

## 跨端契约

涉及两端都要看的"协议 / 命名 / 字段"，统一放在 [shared/](./shared/)。改这些文件**必须两个 app 都有人 review**。

---

## 目录

```
openvox/
├── apps/
│   ├── agentd/           # Node ACP → OpenAI REST 守护进程（agentd CLI）
│   ├── voice-agent/      # Python LiveKit worker（openvox CLI）
│   └── voice-client/     # Flutter 客户端
├── shared/               # 跨端契约（markdown + JSON example）
├── infra/                # LiveKit Server 本地部署
├── tooling/
│   ├── Taskfile.yaml     # 主编排（dev / build / install / release:check）
│   └── scripts/          # build-cli / install-cli / build-client
├── .github/workflows/    # ci.yml（PR 冒烟）+ release.yml（tag → GitHub Release）
├── .gitignore
└── README.md (本文件)
```

---

## 状态

骨架已就绪。`apps/voice-agent` 与 `apps/voice-client` 已迁入代码，可走「快速上手」跑通。后续 backlog 详见 [`openwiki/quickstart.md`](openwiki/quickstart.md) → Backlog。
