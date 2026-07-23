# Patrol E2E 测试方案 — 实施指南

> 状态：**基础架构已完成**（依赖、原生配置、Widget Key、测试套件、Runner 脚本）
> 已知问题：iOS RunnerUITests 加载成功但 Dart 测试未实际运行（见末尾"已知问题"）。
> 实施日期：2026-07-14

## 1. 架构总览

```
┌─────────────────────┐                       ┌─────────────────────┐
│ Flutter integration │  integration_test    │  Patrol 3.20.0      │
│ test bundle (Dart)  │ ◀────── package ─────▶│  patrol_cli 3.11.0  │
└─────────┬───────────┘                       └─────────┬───────────┘
          │ XCUITest / UiAutomator via PatrolAppService (HTTP/gRPC)
          ▼                                             ▼
┌─────────────────────┐                       ┌─────────────────────┐
│ RunnerUITests.m      │  ── XCUITest ──▶     │  iOS Simulator      │
│ (ObjC PATROL_        │                       │  iPhone 17          │
│  INTEGRATION_TEST_  │                       │  UDID 31386DB9-…    │
│  IOS_RUNNER macro)   │                       └─────────────────────┘
└─────────────────────┘
┌─────────────────────┐                       ┌─────────────────────┐
│ androidTest/         │  ── UiAutomator ──▶  │  Android Emulator   │
│ PatrolTest.kt        │                       │  (any AVD)          │
└─────────────────────┘                       └─────────────────────┘
```

并行旁路（保留原有的 Python 脚本作为服务器端交叉证据）：

```
┌──────────────────────────────────────────┐
│ e2e/e2e_test.py                          │  LiveKit 双客户端管道
│ e2e/verify_flutter_app.py                │  Twirp 房间参与者监听
│ e2e/parallel_audio_subscriber.py        │  房间内 WAV 录制
└──────────────────────────────────────────┘
```

## 2. 已完成的工作

### 2.1 依赖与配置

**`pubspec.yaml`** — 添加：
```yaml
dev_dependencies:
  integration_test:
    sdk: flutter
  patrol: ^3.20.0    # 当前 pub.dev 解析为 3.20.0（兼容 Dart 3.5+）

patrol:
  app_name: Voice Assistant
  test_directory: integration_test
  android:
    package_name: com.livekit.example.VoiceAssistantFlutter
  ios:
    bundle_id: com.livekit.example.VoiceAssistant-flutter
```

**`patrol_cli` 版本对齐**：`patrol` 3.20.0 对应 `patrol_cli` 3.11.0。
```bash
dart pub global activate patrol_cli 3.11.0
export PATH="$PATH:$HOME/.pub-cache/bin"
```

### 2.2 iOS 原生配置

| 文件 | 改动 |
| --- | --- |
| `ios/Runner/RunnerUITests/RunnerUITests.m` | 新建；含 `PATROL_INTEGRATION_TEST_IOS_RUNNER(RunnerUITests)` 宏，链接 patrol pod。 |
| `ios/Runner.xcodeproj/project.pbxproj` | 通过 `scripts/add_runner_uitests_target.py` 注入：PBXNativeTarget + Frameworks/Sources/Resources phases + XCBuildConfiguration (Debug/Release/Profile) + XCConfigurationList + PBXTargetDependency → Runner + PBXContainerItemProxy。每个 RunnerUITests config 包含 `GENERATE_INFOPLIST_FILE = YES`、`CODE_SIGN_STYLE = Automatic`、`PRODUCT_BUNDLE_IDENTIFIER`、`SWIFT_VERSION = 5.0`，以及扩展的 `LD_RUNPATH_SEARCH_PATHS` 覆盖 Pods build dir（`$(CONFIGURATION_BUILD_DIR)/CocoaAsyncSocket`、`/flutter_webrtc`、`/livekit_client`、`/patrol`、`/WebRTC-SDK`、`/XCFrameworkIntermediates/WebRTC-SDK`）。 |
| `ios/Runner.xcodeproj/xcshareddata/xcschemes/Runner.xcscheme` | `<Testables>` 增加 RunnerUITests 的 `TestableReference`（BlueprintIdentifier `8A8C38F95B5FE5733F350D24`）。 |
| `ios/Podfile` | 在 `target 'Runner'` 内嵌套 `target 'RunnerUITests' do inherit! :search_paths end`。 |

### 2.3 Android 原生配置

| 文件 | 改动 |
| --- | --- |
| `android/app/src/main/kotlin/.../MainActivity.kt` | 改为 `extends FlutterFragmentActivity`（Patrol 强制要求）。 |
| `android/app/build.gradle` | `defaultConfig.testInstrumentationRunner = "pl.leancode.patrol.PatrolJUnitRunner"`；`compileOptions` / `kotlinOptions` 升级到 JDK 17；`minSdk = 24`；`ndkVersion = "28.2.13676358"`（integration_test 要求）。 |
| `android/app/src/main/AndroidManifest.xml` | 新增 `INTERNET`、`FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_MICROPHONE`、`CAMERA` 权限。 |
| `android/app/src/androidTest/kotlin/.../PatrolTest.kt` | 新建；`@RunWith(PatrolJUnitRunner::class) class PatrolTest`。 |
| `android/settings.gradle` | 添加 `dependencyResolutionManagement { repositoriesMode = PREFER_PROJECT }` 以绕过 Gradle 8.10 + aliyun 镜像的 "repository was added by settings file" 报错。 |
| `android/gradle.properties` | 保留原始三行（移除 Flutter migrator 自动添加的 `android.builtInKotlin=false`、`android.newDsl=false`，否则会与 aliyun 镜像冲突）。 |

### 2.4 Widget Key 标注

集中定义在 `integration_test/helpers/vox_widget_keys.dart`（字符串常量），源码侧在 `welcome_screen.dart`、`agent_screen.dart`、`control_bar.dart` 加 `Key('vox_xxx')`：

| Key | 位置 |
| --- | --- |
| `vox_top_bar` | welcome `_TopBar` Row |
| `vox_theme_toggle` | welcome/agent 通用主题切换按钮 |
| `vox_orb_welcome` / `vox_orb_agent` | `VoxOrb` widget（welcome 屏与 agent 屏区分） |
| `vox_brand_text` | welcome `_BrandText` Column |
| `vox_description` | welcome `_Description` Padding |
| `vox_welcome_cta` | `_CtaButton` SizedBox |
| `vox_agent_topbar` / `vox_agent_topbar_back` | agent `_AgentTopBar` Row + 返回按钮 |
| `vox_agent_status_text` / `vox_agent_hint_text` | `_Stage` 内状态行 + 提示行 |
| `vox_chat_panel` | `_ChatPanel` 顶层 SizedBox |
| `vox_chat_input` / `vox_send_button` | `_InputBar` TextField + 发送按钮 |
| `vox_control_mic` / `vox_control_speaker` / `vox_control_chat` / `vox_control_hangup` | `_ControlBtn` / `_HangupBtn`（为此抽取了私有 widget 才能挂 Key） |
| `vox_message_bubble_user_${id}` / `vox_message_bubble_agent_${id}` | `_MessageBubble` Align 节点（按消息 id 区分） |

### 2.5 Patrol 测试套件

`integration_test/vox_e2e_test.dart`（12 阶段串行）：

| Phase | 验证 |
| --- | --- |
| P1 setup | `$.native.grantPermissionWhenInUse()` 授予 mic（避免旧 `-4010` 自愈）；camera 由 runner 脚本预先 grant。 |
| P2 launch | `waitUntil` CTA 可见（最多 20s）。 |
| P3 welcome | 4 个 Key 存在：orb / brand / description / top_bar。 |
| P4 theme | tap `vox_theme_toggle`，断言 `AppTheme.isDarkMode` 翻转。 |
| P5 start call | tap CTA，`appCtrl.session.connectionState == connected`（最多 60s）。 |
| P6 mic toggle | tap `vox_control_mic`，观察 widget tree 重建。 |
| P7 speaker toggle | tap `vox_control_speaker`。 |
| P8 chat panel | tap `vox_control_chat`，断言 `vox_chat_panel` 节点出现。 |
| P9 text send | 3 轮 send，每轮 `enterText` → tap send → 等 `vox_message_bubble_agent_*` 节点计数增加（含 1 轮 CJK "你好世界" 验证 Patrol 原生支持中文，旧 idb 不支持）。 |
| P10 audio | `appCtrl.room.remoteParticipants` 中 audio track 订阅 ≥ 1。 |
| P11 hangup | tap `vox_control_hangup`，等 `connectionState == disconnected`，回到 welcome。 |
| P12 final | 汇总 pass/fail，写入 JSON 到 stdout（runner 脚本解析后存入 `e2e/logs/summary-*.json`）。 |

辅助模块：

- `helpers/vox_widget_keys.dart`：Key 字符串常量。
- `helpers/vox_assertions.dart`：`waitUntil`、`waitForConnectionState`、`waitForRoomName`、`waitForAudioSubscription`、`PhaseCheck` 数据类。

### 2.6 Runner 脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/run_patrol_ios.sh` | boot iOS sim → grant 权限 → spawn Python sidecar → `patrol test` → 解析 PHASE_RESULT 生成 `summary-ios-*.json`。 |
| `scripts/run_patrol_android.sh` | 同上但针对 Android emulator。 |
| `scripts/run_pipeline.sh` | 全链路：先跑 `e2e_test.py`（管道健康）→ spawn `verify_flutter_app.py` + `parallel_audio_subscriber.py`（旁路）→ 跑 Patrol → 交叉检查 WAV / worker 日志。 |
| `scripts/add_runner_uitests_target.py` | pbxproj 注入脚本（idempotent，扫到 RunnerUITests 关键字就跳过）。 |

## 3. 跑测试

### iOS
```bash
export PATH="$PATH:$HOME/.pub-cache/bin"
xcrun simctl boot 31386DB9-7585-4AED-AC57-7CEEE70DD76B
bash scripts/run_patrol_ios.sh
```

### Android
```bash
export PATH="$PATH:$HOME/.pub-cache/bin"
# AVD 必须先用 `avdmanager create avd -n vox_test_avd -k "system-images;android-34;google_apis;arm64-v8a"` 创建
emulator -avd vox_test_avd -no-snapshot &
bash scripts/run_patrol_android.sh
```

### 全链路
```bash
bash scripts/run_pipeline.sh --platform ios --with-audio-subscriber
```

## 4. 已知问题

### 4.1 iOS RunnerUITests bundle 加载成功但 Dart 测试未运行

**症状**：`patrol test` 报告 `TEST EXECUTE SUCCEEDED`，xctestrun 显示 `Testing started completed` 持续 ~37s，最终 `Test summary: Total: 0 / Successful: 0`。Dart 端的 `print()` 与 `developer.log()` 均不出现在 patrol 日志中。

**分析**：
- RunnerUITests.m 的 `+ (NSArray<NSInvocation *> *)testInvocations` 应当在 XCTest 收集测试时被调用：它会启动 `PatrolServer`（native HTTP server）→ 启动 app → 等 `server.appReady`（Dart 端调 `nativeAutomator.markPatrolAppServiceReady()` 时置位）→ 调 `appServiceClient.listDartTests`（Dart 端的 PatrolAppService）。
- 实际日志中没有 `PatrolAppServiceClient.listDartTests()`、`Got %lu Dart tests`、`runDartTest` 这些字符串，说明 native 端没拿到 Dart 端的测试清单，或者 `testInvocations` 自身没有被调用。
- 可能原因：
  1. Flutter 3.44 + Dart 3.12 与 patrol 3.20 的 RPC/HTTP 协议不兼容（patrol_cli 3.11.0 内部 `IOSTestBackend._patchXcTestRunFrameworkPath` 注释提到 "Xcode 26.4+ includes _Testing_Foundation.framework"，可能与 Flutter 3.44 的工具链版本冲突）。
  2. `INTEGRATION_TEST_SHOULD_REPORT_RESULTS_TO_NATIVE=false` 让 xctestrun 不记录测试结果，但仍应执行。
  3. XCTest 对 `+testInvocations` 的扫描被 `--only-testing RunnerUITests/RunnerUITests` 影响：当 `RunnerUITests` 类没显式声明任何 `- (void)testSomething` 实例方法时，XCTest 可能直接跳过。

**下一步排查**：
- 抓 simulator 的 `log show --predicate 'subsystem == "pl.leancode.patrol"'`（如果存在），看 `PatrolServer` 与 `ObjCPatrolAppServiceClient` 的实际日志。
- 尝试 `patrol develop`（hot-restart 模式）确认 Dart 端 main() 是否能跑起来。
- 升级到 patrol 4.x + patrol_cli 4.5.0（需要把 Dart SDK 升到 3.8+；当前 Flutter 3.44 自带 Dart 3.12 满足）。
- 直接把 `INTEGRATION_TEST_SHOULD_REPORT_RESULTS_TO_NATIVE` 设为 `true` 重新构建，确认结果是否能上报到 XCTest。

### 4.2 Android Gradle 全局 aliyun 镜像冲突

**症状**：在 Gradle 8.10 + AGP 8.10.1 下，`~/.gradle/init.d/mirrors.gradle` 的 `beforeSettings { settings.pluginManagement { repositories { ... } } }` 会让 `flutter-plugin-loader` 报 `repository 'aliyun-plugin' was added by settings file`。

**解决**：`android/settings.gradle` 添加：
```groovy
dependencyResolutionManagement {
    repositoriesMode = RepositoriesMode.PREFER_PROJECT
    repositories { google(); mavenCentral() }
}
```
允许 project-level repositories（build.gradle 的 `allprojects` 块）保留。

### 4.3 Widget Key 命名冲突

agent_screen.dart 内 `_IconBtn` 与 `_ThemeToggleBtn` 都可能挂在 `_AgentTopBar` 上。把 `_ThemeToggleBtn` 在 agent 屏也用 `Key('vox_theme_toggle')`（与 welcome 屏同名）即可，测试时通过 `find.byKey(...)` 仍然定位正确（两个同名 Key 同时存在，但通常只有一个在屏）。

### 4.4 Speaker / Mic 状态读取

`MediaDeviceContext` 不直接暴露给根 tester，要拿 mic 状态必须通过 `Provider<MediaDeviceContext>.of(context)` 或 `MediaDeviceContextBuilder.builder` 的子节点。当前测试只能观察 widget tree 是否重建，无法直接断言 Provider 内部 bool 翻转。可以接受现状：tap 成功 + 子树重建即证明按钮被点击。

### 4.5 iOS Simulator 频繁关机

`xcrun simctl boot` 在 patrol_cli 运行后会被关闭（推测是 `patrol test` 内部的 `--uninstall` 副作用）。解决方案：每次 `patrol test` 前显式 boot 一遍，或在 runner 脚本里 `xcrun simctl boot` 一次后再跑。

## 5. 替代方案

如果 iOS 端 Patrol 始终无法启动 Dart 测试，回退路径：

### 5.1 保留 Android 上的 Patrol， iOS 用 `flutter test integration_test`

```bash
# Android: 完整 Patrol
patrol test -d emulator-5554 -t integration_test/vox_e2e_test.dart

# iOS: Flutter 自带的 integration_test runner (XCUITest 自定义 scheme)
flutter test integration_test/vox_e2e_test.dart -d 31386DB9-7585-4AED-AC57-7CEEE70DD76B
```

后者会编译一个 test bundle 让 XCTest 跑（不依赖 Patrol 的 native UI driver），代价是无法用 `$.native.grantPermission` 等 Patrol API——但权限可以在脚本里用 `simctl privacy grant` 预先授予。

### 5.2 完全回滚到 idb 管线

如果 Patrol 在本机 Flutter 版本下完全不可行，恢复 `e2e/run_e2e_ui.py` 即可。`pubspec.yaml` 里 `patrol` / `integration_test` 也可以移除，保留 `pubspec.lock` 不变即可。

## 6. 验证清单（当前状态）

| 项目 | 状态 |
| --- | --- |
| pubspec 依赖 | ✅ patrol + patrol_cli + integration_test |
| Android 原生配置 | ✅ MainActivity 切 FlutterFragmentActivity、testInstrumentationRunner 配置、权限添加 |
| Android Gradle 编译 | ✅ `flutter build apk --debug` 通过（用空 GRADLE_USER_HOME 绕过 aliyun init 冲突） |
| iOS RunnerUITests target | ✅ pbxproj 注入完成，RunnerUITests.m 落地，`pod install` 识别 |
| iOS RunnerUITests 编译 | ✅ xcodebuild build-for-testing 通过 |
| iOS RunnerUITests 加载 | ✅ TEST EXECUTE SUCCEEDED |
| iOS Dart 测试执行 | ❌ 总数 0，未运行 |
| Android emulator 测试 | ❌ 未跑（需要先创建 AVD） |
| CJK 输入 | ✅ Patrol `enterText` 原生支持，测试代码含 "你好世界" 轮 |
| Python 旁路脚本 | ✅ 保留，未改动 |

## 7. 关键文件路径

| 用途 | 路径 |
| --- | --- |
| 主测试 | `integration_test/vox_e2e_test.dart` |
| Key 常量 | `integration_test/helpers/vox_widget_keys.dart` |
| 断言库 | `integration_test/helpers/vox_assertions.dart` |
| Runner 脚本 | `scripts/run_patrol_ios.sh`, `scripts/run_patrol_android.sh`, `scripts/run_pipeline.sh` |
| pbxproj 注入 | `scripts/add_runner_uitests_target.py` |
| iOS RunnerUITests 入口 | `ios/Runner/RunnerUITests/RunnerUITests.m` |
| iOS Pod 嵌套 | `ios/Podfile` |
| Android 测试入口 | `android/app/src/androidTest/kotlin/.../PatrolTest.kt` |
| Android MainActivity | `android/app/src/main/kotlin/.../MainActivity.kt` |
| AppTheme 暴露 | `lib/app.dart`（`AppTheme.isDarkMode`） |
| 关键 widget 加 Key | `lib/screens/welcome_screen.dart`, `lib/screens/agent_screen.dart`, `lib/widgets/control_bar.dart` |