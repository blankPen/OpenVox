package com.livekit.example.VoiceAssistantFlutter

import org.junit.runner.RunWith
import pl.leancode.patrol.PatrolJUnitRunner

/**
 * Patrol instrumentation test entry point.
 *
 * patrol_cli compiles [integration_test]/*_test.dart into a Flutter test bundle,
 * pushes it onto the device under test, and uses [PatrolJUnitRunner] to drive
 * the Android side. The runner talks to the Flutter app via `PatrolMethodChannel`
 * to exchange commands (tap coordinates, take screenshot, etc.).
 *
 * The actual test cases live in the Dart files under `integration_test/`. This
 * Kotlin class is intentionally empty — Patrol does not surface any
 * test methods of its own; it executes the Dart test bundle entirely from
 * native code.
 */
@RunWith(PatrolJUnitRunner::class)
class PatrolTest