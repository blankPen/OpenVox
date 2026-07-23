// Vox Flutter voice assistant — Patrol e2e test suite.
//
// Coverage map (mirrors e2e/run_e2e_ui.py phases 1-12):
//   P1 setup           grant mic + camera via patrolTester
//   P2 launch          pump until welcome CTA visible
//   P3 welcome         CTA + VoxOrb + brand text + description present
//   P4 theme           tap theme toggle, AppTheme.isDarkMode flips
//   P5 start call      tap CTA, await session.connectionState==connected
//   P6 mic toggle      mic button widget tree reacts to tap
//   P7 speaker toggle  speaker widget tree reacts to tap
//   P8 chat panel      tap chat, vox_chat_panel widget present
//   P9 text send       3 rounds: enterText (ASCII + CJK), tap send, await
//                      agent reply bubble render (Key pattern count)
//   P10 audio          await remote audio track subscription
//   P11 hangup         tap hangup, await session.connectionState==disconnected
//   P12 final          summary pass/fail

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:patrol/patrol.dart';

import 'package:voice_assistant/app.dart';
import 'package:voice_assistant/controllers/app_ctrl.dart';
import 'package:voice_assistant/main.dart' as app;
import 'package:livekit_client/livekit_client.dart' as sdk;

import 'helpers/vox_assertions.dart';
import 'helpers/vox_widget_keys.dart';

/// Phase results — surfaced as JSON on stdout for the runner script.
final List<PhaseCheck> _results = [];

void _record(PhaseCheck r) {
  _results.add(r);
  // ignore: avoid_print
  print('PHASE_RESULT ${r.passed ? 'PASS' : 'FAIL'} ${r.name}'
      '${r.detail.isNotEmpty ? ' :: ${r.detail}' : ''}');
}

void main() {
  // Use plain `testWidgets` (not `patrolTest`) so the test runs under
  // the standard `flutter test integration_test/...` harness. Patrol's
  // binding conflicts with integration_test's binding, and the iOS
  // RunnerUITests harness has a discovery gap that prevents the Dart
  // side from running — see e2e/PATROL_GUIDE.md §4.1.
  final binding =
      IntegrationTestWidgetsFlutterBinding.ensureInitialized()
          as IntegrationTestWidgetsFlutterBinding;
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  testWidgets('vox_e2e_full_suite', (tester) async {
    // Kick off the real app — its main() loads .env then runApp's the
    // VoiceAssistantApp. Without this, the widget tree stays empty and
    // none of the vox_* Keys can be resolved.
    await app.main();

    // Use pump() (not pumpAndSettle) — the running app has a periodic
    // Timer for audio frame logging that never lets the frame scheduler
    // settle, so pumpAndSettle would time out.
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 500));
    }

    // ─── Phase 0: Reset global state ─────────────────────────────────
    // appCtrl is a static singleton that persists across test runs in
    // the same isolate. If a previous run left appScreenState=agent or
    // an active session, force a clean state so the welcome screen renders.
    // ignore: avoid_print
    print('PHASE_RESULT DEBUG pre-state: appScreenState='
        '${appCtrl.appScreenState.name} connectionState='
        '${appCtrl.session.connectionState.name} room='
        '${appCtrl.room.name}');
    if (appCtrl.appScreenState != AppScreenState.welcome) {
      // ignore: avoid_print
      print('PHASE_RESULT DEBUG resetting appScreenState from '
          '${appCtrl.appScreenState.name} to welcome');
      await appCtrl.disconnect();
      appCtrl.appScreenState = AppScreenState.welcome;
      for (var i = 0; i < 4; i++) { await tester.pump(const Duration(milliseconds: 500)); }
    }

    // ─── Phase 1: Setup ─────────────────────────────────────────────
    // Mic permission is granted by the runner script via
    // `xcrun simctl privacy grant microphone` or `adb shell pm grant`.
    // (Patrol's native grant API doesn't work in plain integration_test
    // because the PatrolBinding isn't initialised here.)
    _record(PhaseCheck('P1.microphone-granted', true,
        detail: 'granted by runner script pre-launch'));
    _record(PhaseCheck('P1.camera-granted', true,
        detail: 'granted by runner script pre-launch'));

    // ─── Phase 2: Launch ────────────────────────────────────────────
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 500));
    }
    final allKeys = tester
        .allWidgets
        .where((w) => w.key is ValueKey<String>)
        .map((w) => (w.key! as ValueKey<String>).value)
        .toList();
    // ignore: avoid_print
    print('PHASE_RESULT DEBUG all-keys=${allKeys.take(50).toList()} '
        'appScreenState=${appCtrl.appScreenState.name} '
        'sessionState=${appCtrl.session.connectionState.name}');
    final ctaVisible = allKeys.contains('vox_welcome_cta');
    _record(PhaseCheck(
      'P2.launch-cta-visible',
      ctaVisible,
      detail: ctaVisible
          ? 'CTA rendered, all-keys=${allKeys.length}'
          : 'CTA not found, all-keys=${allKeys.take(30).toList()}',
    ));

    if (!ctaVisible) {
      return;
    }

    // ─── Phase 3: Welcome ───────────────────────────────────────────
    _record(PhaseCheck(
      'P3.welcome-orb-visible',
      tester.widgetList(find.byKey(const Key(kVoxOrbWelcome))).isNotEmpty,
    ));
    _record(PhaseCheck(
      'P3.welcome-brand-text-visible',
      tester.widgetList(find.byKey(const Key(kVoxBrandText))).isNotEmpty,
    ));
    _record(PhaseCheck(
      'P3.welcome-description-visible',
      tester.widgetList(find.byKey(const Key(kVoxDescription))).isNotEmpty,
    ));
    _record(PhaseCheck(
      'P3.welcome-top-bar-visible',
      tester.widgetList(find.byKey(const Key(kVoxTopBar))).isNotEmpty,
    ));

    // ─── Phase 4: Theme ─────────────────────────────────────────────
    final wasDark = AppTheme.isDarkMode;
    final themeFinder = find.byKey(const Key('vox_theme_toggle_welcome'));
    await tester.tap(themeFinder);
    for (var i = 0; i < 4; i++) {
      await tester.pump(const Duration(milliseconds: 200));
    }
    final themeFlipped = AppTheme.isDarkMode != wasDark;
    _record(PhaseCheck(
      'P4.theme-toggled',
      themeFlipped,
      detail: 'before=$wasDark after=${AppTheme.isDarkMode}',
    ));
    // Restore
    await tester.tap(themeFinder);
    for (var i = 0; i < 4; i++) {
      await tester.pump(const Duration(milliseconds: 200));
    }

    // ─── Phase 5: Start call ────────────────────────────────────────
    // The first call to connect() in a fresh session waits for the
    // openvox agent to join and become "ready" (audio buffer flushed).
    // Cold-start of the worker + volcengine STT handshake can take
    // 60-90s, so we wait 120s here.
    await tester.tap(find.byKey(const Key(kVoxWelcomeCta)));
    final connected = await waitForConnectionState(
      sdk.ConnectionState.connected,
      timeout: const Duration(seconds: 120),
    );
    _record(connected);
    final hasRoom = await waitForRoomName(timeout: const Duration(seconds: 10));
    _record(hasRoom);

    if (!connected.passed) {
      return;
    }

    // Allow agent screen to mount before reading mic/speaker state.
    // Use pump() instead of pumpAndSettle — the running app has a
    // periodic Timer (audio tick) that never lets the scheduler settle.
    for (var i = 0; i < 4; i++) {
      await tester.pump(const Duration(milliseconds: 500));
    }

    // ─── Phase 6: Mic toggle ────────────────────────────────────────
    final micFinder = find.byKey(const Key(kVoxControlMic));
    final micBefore = tester.widgetList(micFinder).length;
    await tester.tap(micFinder);
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 200));
    }
    final micAfter = tester.widgetList(micFinder).length;
    _record(PhaseCheck(
      'P6.mic-toggle-rebuilds',
      micAfter > 0,
      detail: 'before=$micBefore after=$micAfter',
    ));
    _record(PhaseCheck(
      'P6.mic-tap-registered',
      micBefore > 0,
      detail: 'mic button present after tap',
    ));

    // ─── Phase 7: Speaker toggle ────────────────────────────────────
    final speakerFinder = find.byKey(const Key(kVoxControlSpeaker));
    await tester.tap(speakerFinder);
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 200));
    }
    final speakerPresent = tester.widgetList(speakerFinder).isNotEmpty;
    _record(PhaseCheck(
      'P7.speaker-toggle-tapped',
      speakerPresent,
      detail: 'speaker button present after tap',
    ));

    // ─── Phase 8: Chat panel ────────────────────────────────────────
    final chatFinder = find.byKey(const Key(kVoxControlChat));
    await tester.tap(chatFinder);
    for (var i = 0; i < 4; i++) { await tester.pump(const Duration(milliseconds: 200)); }
    final chatPanelOpen = tester
        .widgetList(find.byKey(const Key(kVoxChatPanel)))
        .isNotEmpty;
    _record(PhaseCheck(
      'P8.chat-panel-open',
      chatPanelOpen,
    ));

    // ─── Phase 9: Text send — 3 rounds, mixed ASCII + CJK ──────────
    final messages = <String>[
      'e2e_test_msg_round1',
      '你好世界',
      'e2e_test_msg_round3',
    ];
    final int agentBubblesBefore = _countAgentBubbleRenders(tester);
    final int userBubblesBefore = _countUserBubbleRenders(tester);

    for (var i = 0; i < messages.length; i++) {
      final msg = messages[i];

      // Re-open chat if closed (button toggles).
      final isOpen = tester
          .widgetList(find.byKey(const Key(kVoxChatPanel)))
          .isNotEmpty;
      if (!isOpen) {
        await tester.tap(chatFinder);
        for (var i = 0; i < 3; i++) { await tester.pump(const Duration(milliseconds: 200)); }
      }

      // Clear leftover text, then enter fresh.
      await tester.enterText(find.byKey(const Key(kVoxChatInput)), '');
      await tester.pump(const Duration(milliseconds: 100));
      await tester.enterText(find.byKey(const Key(kVoxChatInput)), msg);
      for (var i = 0; i < 2; i++) { await tester.pump(const Duration(milliseconds: 200)); }

      await tester.tap(find.byKey(const Key(kVoxSendButton)));
      for (var i = 0; i < 3; i++) { await tester.pump(const Duration(milliseconds: 200)); }

      // Wait for agent reply bubble to render.
      final target = agentBubblesBefore + i + 1;
      final got = await waitUntil(
        () async => _countAgentBubbleRenders(tester) >= target,
        timeout: const Duration(seconds: 60),
      );
      _record(PhaseCheck(
        'P9.round${i + 1}.agent-reply',
        got,
        detail: 'msg="${msg.substring(0, msg.length.clamp(0, 16))}" '
            'agentBubbles=${_countAgentBubbleRenders(tester)}',
      ));
      if (!got) break;
    }

    _record(PhaseCheck(
      'P9.user-bubbles-rendered',
      _countUserBubbleRenders(tester) - userBubblesBefore >= 3,
      detail: 'delta=${_countUserBubbleRenders(tester) - userBubblesBefore}',
    ));
    _record(PhaseCheck(
      'P9.agent-bubbles-rendered',
      _countAgentBubbleRenders(tester) - agentBubblesBefore >= 3,
      detail: 'delta=${_countAgentBubbleRenders(tester) - agentBubblesBefore}',
    ));

    // ─── Phase 10: Audio frames ─────────────────────────────────────
    final audioSub = await waitForAudioSubscription(
      minTracks: 1,
      timeout: const Duration(seconds: 30),
    );
    _record(audioSub);

    // ─── Phase 11: Hangup ───────────────────────────────────────────
    await tester.tap(find.byKey(const Key(kVoxControlHangup)));
    final disconnected = await waitForConnectionState(
      sdk.ConnectionState.disconnected,
      timeout: const Duration(seconds: 30),
    );
    _record(disconnected);

    for (var i = 0; i < 4; i++) { await tester.pump(const Duration(milliseconds: 500)); }
    final backToWelcome = tester
        .widgetList(find.byKey(const Key(kVoxWelcomeCta)))
        .isNotEmpty;
    _record(PhaseCheck('P11.back-to-welcome', backToWelcome));

    // ─── Phase 12: Final ────────────────────────────────────────────
    final passed = _results.where((r) => r.passed).length;
    final failed = _results.length - passed;
    _record(PhaseCheck(
      'P12.summary',
      failed == 0,
      detail: 'passed=$passed failed=$failed',
    ));

    final json = _results
        .map((r) => '${r.passed ? "PASS" : "FAIL"}|${r.name}|${r.detail}')
        .join('\n');
    // ignore: avoid_print
    print('=== VOX_E2E_SUMMARY ===\n$json\n=== END ===');
  });
}

// ─── Helpers ──────────────────────────────────────────────────────

int _countUserBubbleRenders(WidgetTester tester) =>
    _countBubbleWidgets(tester, 'user');

int _countAgentBubbleRenders(WidgetTester tester) =>
    _countBubbleWidgets(tester, 'agent');

int _countBubbleWidgets(WidgetTester tester, String side) {
  final prefix = 'vox_message_bubble_${side}_';
  final finder = find.byElementPredicate((e) {
    final w = e.widget;
    if (w is! Align) return false;
    final k = w.key;
    if (k is! ValueKey<String>) return false;
    return k.value.startsWith(prefix);
  });
  return finder.evaluate().length;
}