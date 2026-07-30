#!/usr/bin/env bash
# build-client.sh — Build the Flutter voice-client for one or more platforms.
#
# Usage:
#   ./tooling/scripts/build-client.sh android
#   ./tooling/scripts/build-client.sh ios
#   ./tooling/scripts/build-client.sh all       # android + ios (host must support both)
#
# Notes:
#   - iOS requires macOS + Xcode + CocoaPods.
#   - Android requires Flutter + Android SDK + JDK 17.
#   - Use FLUTTER_BIN to point at a non-default flutter binary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/log.sh
. "$SCRIPT_DIR/lib/log.sh"

FLUTTER_BIN="${FLUTTER_BIN:-flutter}"
TARGET="${1:-all}"

run_flutter() {
  # --no-version-check avoids a Tsinghua-mirror git fetch that hangs on some networks.
  "$FLUTTER_BIN" --no-version-check "$@"
}

build_android() {
  step "voice-client android (apk, debug)"
  if ! have "$FLUTTER_BIN" && ! command -v "$FLUTTER_BIN" >/dev/null 2>&1; then
    die "flutter not found in PATH (set FLUTTER_BIN to override)"
  fi
  cd "$REPO_ROOT/apps/voice-client"
  run_flutter pub get
  run_flutter build apk --debug

  local apk="build/app/outputs/flutter-apk/app-debug.apk"
  [[ -f "$apk" ]] || die "apk not found at $apk"
  info "android apk: $apk ($(du -h "$apk" | awk '{print $1}'))"
}

build_ios() {
  step "voice-client ios (debug, no codesign)"
  if [[ "$(uname -s)" != "Darwin" ]]; then
    die "ios build requires macOS (current: $(uname -s))"
  fi
  if ! have xcodebuild; then die "xcodebuild not found"; fi
  if ! have pod; then die "pod (CocoaPods) not found"; fi

  cd "$REPO_ROOT/apps/voice-client"
  run_flutter pub get
  # --simulator builds the iPhoneSimulator slice (no codesigning required);
  # --no-codesign produces the iphoneos slice without provisioning profiles.
  run_flutter build ios --debug --no-codesign --simulator

  local app_dir="build/ios/iphonesimulator/Runner.app"
  [[ -d "$app_dir" ]] || die "Runner.app not found at $app_dir"
  info "ios simulator .app: $app_dir ($(du -sh "$app_dir" | awk '{print $1}'))"
}

case "$TARGET" in
  android) build_android ;;
  ios) build_ios ;;
  all|"") build_android; build_ios ;;
  *) die "unknown target: $TARGET (use android | ios | all)";;
esac

step "done"
