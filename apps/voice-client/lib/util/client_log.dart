import 'dart:developer' as developer;
import 'package:flutter/foundation.dart';

/// 客户端结构化日志 helper。
///
/// 输出格式：`[Client] <tag> <message>`，同时打到 flutter debugPrint 和
/// OS log（macOS Console.app / `xcrun simctl spawn booted log stream` 可见）。
/// e2e 测试通过 grep 这个前缀验证客户端事件。
class ClientLog {
  /// 一条结构化事件。tag 是事件名（如 "connect" / "mic" / "text"），message
  /// 是参数化描述。e2e 用 `[Client] <tag> <前缀>` 做精确匹配。
  static void event(String tag, String message) {
    final line = '[$tag] $message';
    debugPrint('[Client] $line');
    developer.log(line, name: 'vox.client');
  }

  /// 每秒由 audio subscription 回调调用一次，避免每帧打日志。
  /// `frames` 当前为订阅 audio track 数量（非真实 frame count，SDK 不暴露）。
  static void audioTick(int frames, String from) {
    debugPrint('[Client] audio recv frames=$frames from=$from');
  }
}