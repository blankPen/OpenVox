// Smoke test — verifies the patrol harness actually runs our test code
// by writing a marker file at multiple points. If you see this file with
// the expected markers after `patrol test`, the integration is working.

import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:patrol/patrol.dart';

void main() {
  void mark(String tag) {
    // ignore: avoid_print
    print('VOX_SMOKE: $tag');
    developer.log('VOX_SMOKE: $tag', name: 'vox.smoke');
  }

  mark('main() entered');
  final binding =
      IntegrationTestWidgetsFlutterBinding.ensureInitialized()
          as IntegrationTestWidgetsFlutterBinding;
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;
  mark('binding initialized');

  testWidgets('test_vox_smoke', (tester) async {
    mark('testWidgets entered');
    await tester.pumpAndSettle(const Duration(seconds: 1));
    mark('pumpAndSettle done');
    expect(tester.binding, isNotNull);
    mark('expect done');
  });
}