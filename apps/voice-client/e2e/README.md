# Vox Flutter e2e Test Suite

> **2026-07 更新**：新增 Patrol-based 测试套件（`integration_test/`）作为
> 主测试驱动，原 idb-based 脚本（`run_e2e_ui.py`）保留为旁路 cross-check。
> 详见 [`PATROL_GUIDE.md`](PATROL_GUIDE.md) 与 `scripts/run_patrol_*.sh` / `scripts/run_pipeline.sh`。

Two complementary e2e tests for the Vox voice assistant:

| Script | Layer | What it tests |
| --- | --- | --- |
| `e2e_test.py` | LiveKit pipeline (Python SDK) | Token sign, Twirp, two-client connect/publish/subscribe, server verify |
| `run_e2e_ui.py` | Flutter UI (idb) | Full app walkthrough on iOS simulator: welcome → theme → start call → mic toggle → chat panel → text send → hangup |

`verify_flutter_app.py` is a Twirp listener (run alongside the Flutter app) to confirm the client actually reaches `openz-room`.

## run_e2e_ui.py — UI walkthrough

Drives the real app on iPhone 17 simulator via `idb` (tap / text / screenshot). Each step takes a screenshot and asserts a visual signal. Re-runnable: re-installs the app, regrants mic permission, force-toggles to known state.

### Phases

| # | Phase | Verifies |
| --- | --- | --- |
| 1 | Setup | sim booted, app installed, mic + camera permission granted, idb connected |
| 2 | Launch | app rendered (screenshot > 300 KB) |
| 3 | Welcome | CTA gradient color + orb visible |
| 4 | Theme | dark/light mode (top-left bg pixel) |
| 5 | Start call | peach mic button = on agent screen; self-heals on -4010 |
| 6 | Mic toggle | mic button bg lightens (muted) → restores (active) |
| 7 | Chat panel | chat button bg = peach when panel open |
| 8 | Text input + send | send button = warm gradient after typing |
| 9 | Hangup | back to welcome (CTA gradient visible) |

### Run

```bash
# Prereqs:
#   brew install idb-companion
#   pip3 install --user fb-idb
#   iPhone 17 simulator booted

python3 e2e/run_e2e_ui.py            # all 9 phases
python3 e2e/run_e2e_ui.py --quick    # setup + launch + welcome + start + hangup
python3 e2e/run_e2e_ui.py --phase 5  # just start call
```

### Self-healing

- **-4010 mic permission** (LiveKit Flutter SDK's "Unable to start local recording"): the script auto-grants permission, terminates the app, relaunches, and retries the CTA tap.
- **Stale agent state** (app relaunches into agent screen with chat panel still open from a previous session): `force_to_welcome` taps hangup until the CTA gradient is visible.
- **Chat panel flakiness**: `force_chat_open` taps the chat button and re-checks the bg color until the panel is confirmed open.

### Output

- `e2e/screenshots/` — every screenshot taken, named per phase
- `e2e/logs/summary-YYYYMMDD-HHMMSS.json` — JSON report with per-check pass/fail and detail strings

### Known limitations

- **CJK input not supported by `idb ui text`**: it uses HID keycodes, which are ASCII only. We type `e2e_test_msg` (ASCII) to exercise the input pipeline. Chinese UI text is verified by screenshot inspection.
- **File-size proxy is unreliable on orb-heavy states**: when the orb is in "speaking" state with full gradient halo, the screenshot is ~1 MB even with chat closed. We use *color* checks (chat button bg) as the primary signal and file size as a secondary cross-check.
- **Single simulated iPhone 17**: hardcoded UDID `31386DB9-7585-4AED-AC57-7CEEE70DD76B`.

## Verifying the e2e test is doing real work

`e2e/screenshots/phase_start_2_*.png` (or similar) should show a real LiveKit session with:
- Top bar: "● 通话中 MM:SS" with a running timer
- The orb in `listening` / `thinking` / `speaking` states (cycles every 4s)
- 4 control buttons at the bottom

When chat is open, the same screenshots show a frosted-glass panel with the message history (timestamps 20:30:00, 20:30:12, ...) and an input bar at the bottom with "输入文字消息…" placeholder.

If you see only the welcome screen (`开始语音通话 →` button), the start-call step failed — check `/tmp/livekit-worker.log` for the agent's view.
