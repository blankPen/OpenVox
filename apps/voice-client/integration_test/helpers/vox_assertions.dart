/// Phase-level assertion helpers built on top of Patrol's `patrolTester`.
///
/// These helpers exist because:
///   1. The original idb-based e2e script used pixel sampling, which
///      breaks whenever the orb changes gradient or a font swap lands.
///   2. With Patrol we can read widget state directly via Provider and
///      skip the screenshot round-trip entirely.
///   3. CJK input is natively supported by `patrolTester.enterText`,
///      unlike `idb ui text` which only emits ASCII HID keycodes.
library;

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:voice_assistant/app.dart';
import 'package:livekit_client/livekit_client.dart' as sdk;

/// Result of a phase check, mirroring the JSON-friendly shape the Python
/// run_e2e_ui.py emits so summaries stay consistent.
class PhaseCheck {
  PhaseCheck(this.name, this.passed, {this.detail = ''});
  final String name;
  final bool passed;
  final String detail;
}

/// Wait until [predicate] returns true OR [timeout] elapses. Polls every
/// 200ms which keeps the event loop responsive (Patrol taps remain
/// processable during the wait).
Future<bool> waitUntil(
  Future<bool> Function() predicate, {
  Duration timeout = const Duration(seconds: 30),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(deadline)) {
    if (await predicate()) return true;
    await Future<void>.delayed(const Duration(milliseconds: 200));
  }
  return predicate();
}

/// Wait until the LiveKit session reaches [expected]. Returns true on success.
Future<PhaseCheck> waitForConnectionState(
  sdk.ConnectionState expected, {
  Duration timeout = const Duration(seconds: 30),
}) async {
  final ok = await waitUntil(
    () async => appCtrl.session.connectionState == expected,
    timeout: timeout,
  );
  return PhaseCheck(
    'session.connectionState==${expected.name}',
    ok,
    detail: 'actual=${appCtrl.session.connectionState.name}',
  );
}

/// Wait until the room name is non-empty (server has accepted the connection).
Future<PhaseCheck> waitForRoomName({Duration timeout = const Duration(seconds: 30)}) async {
  final ok = await waitUntil(
    () async {
      final name = appCtrl.room.name;
      return name != null && name.isNotEmpty;
    },
    timeout: timeout,
  );
  return PhaseCheck(
    'room.name non-empty',
    ok,
    detail: ok ? 'room=${appCtrl.room.name}' : 'room is still empty',
  );
}

/// Wait until `_subscribedAudioTracks` is at least [minTracks]. Used to prove
/// the Flutter SDK received the agent's TTS audio track.
Future<PhaseCheck> waitForAudioSubscription({
  int minTracks = 1,
  Duration timeout = const Duration(seconds: 30),
}) async {
  // Access private counter via runtime mirror — AppCtrl exposes
  // _subscribedAudioTracks as a private int. We don't want to widen the
  // surface just for tests, so we read it through a tick log instead.
  // For now we just poll appCtrl.session.events for track subscriptions.
  // Implementation: rely on the [ClientLog] 'audio recv frames=' marker
  // pumped every second by AppCtrl — Patrol's tester can't intercept that
  // from Dart, so we just poll room state.
  //
  // Practical: we count remote audio tracks via room.remoteParticipants.
  final ok = await waitUntil(
    () async {
      final tracks = <sdk.Track>[];
      for (final p in appCtrl.room.remoteParticipants.values) {
        tracks.addAll(p.trackPublications.values
            .where((pub) => pub.kind == sdk.TrackType.AUDIO)
            .map((pub) => pub.track)
            .whereType<sdk.Track>());
      }
      return tracks.length >= minTracks;
    },
    timeout: timeout,
  );
  return PhaseCheck(
    'remote audio tracks >= $minTracks',
    ok,
    detail: ok
        ? 'subscribed'
        : 'no remote audio tracks after ${timeout.inSeconds}s',
  );
}

/// Tap a widget identified by [keyName], with a short settle afterwards.
Future<void> tapByKey(WidgetTester tester, String keyName) async {
  await tester.tap(find.byKey(Key(keyName)));
  await tester.pumpAndSettle(const Duration(milliseconds: 300));
}

/// Pump frames until [waitUntil] returns true OR [timeout] elapses.
Future<void> pumpUntil(
  WidgetTester tester,
  Future<bool> Function() waitUntil, {
  Duration timeout = const Duration(seconds: 30),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 200));
    if (await waitUntil()) return;
  }
}