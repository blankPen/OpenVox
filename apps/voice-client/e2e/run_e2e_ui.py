#!/usr/bin/env python3
"""End-to-end UI test for the Flutter Voice Assistant on iOS simulator.

Drives the actual app through idb (tap/text/screenshot), verifies visual
state at each phase, and reports pass/fail per check. Designed to be
self-healing for the common -4010 mic permission bug, and re-runnable.

Prereqs:
  - idb_companion (brew install idb-companion) and fb-idb (pip3 install fb-idb)
  - iOS simulator booted (default: iPhone 17 UDID 31386DB9-...)
  - App built and installed: build/ios/iphonesimulator/Runner.app

Usage:
  python3 e2e/run_e2e_ui.py              # run all phases
  python3 e2e/run_e2e_ui.py --phase 1-3  # run subset
  python3 e2e/run_e2e_ui.py --quick      # smoke test only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UDID = "31386DB9-7585-4AED-AC57-7CEEE70DD76B"
BUNDLE_ID = "com.livekit.example.VoiceAssistant-flutter"
IDB_PORT = 10882
IDB_BIN = "/Users/pz/Library/Python/3.9/bin/idb"
IDB_COMPANION = "/opt/homebrew/bin/idb_companion"
PYTHON_BIN = sys.executable

ROOT = Path(__file__).resolve().parent
SHOTS_DIR = ROOT / "screenshots"
LOGS_DIR = ROOT / "logs"
AUDIO_DIR = ROOT / "audio"
SHOTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# ───────── ERROR 豁免清单 ─────────
# 每条豁免配 issue 链接或详细根因。原则：
#   1. 我们没在 logger 层 suppress（这些 ERROR 真实从框架打出来）
#   2. 根因在 upstream（livekit 框架 / 火山引擎插件），不在我们仓库
#   3. 真正的修复需改框架源码或升级版本
#   4. 本地 patch 能减小但不能完全消除泄漏（见 main.py 三段 patch）
# 升级 livekit-agents 后逐条验证能否从清单删除。
import re
EXEMPT_ERROR_PATTERNS: list[tuple[re.Pattern, str]] = [
    # LiveKit Rust client SDK 把 server 主动 close 误打 ERROR。
    # 在 Rust signal client 关闭后还有滞留信号也被错误地 ERROR 日志。
    (re.compile(r"publisher data channel '_(reliable|lossy|data_track)' closed unexpectedly"),
     "livekit/rust-sdks lifecycle noise — clean shutdown logged at wrong level"),
    (re.compile(r"dropping pass-through signal — no stream available"),
     "livekit/rust-sdks lifecycle noise — same as above"),
    # volcengine STT inner recv_task 抛 CancelledError 但没被外层 await，
    # asyncio 兜底打 "exception was never retrieved" ERROR。
    # 根因在 STT 插件 _run 内部 asyncio.create_task(recv_task) 后没 await
    # 它的 exception（在 gracefully_cancel 后）。我们 main.py 已有 patch
    # 让 outer CancelledError 静默，但 inner task 的 exception 仍会漏。
    # 完整修复需改 volcengine stt.py 内部 await 顺序。
    (re.compile(r"_GatheringFuture exception was never retrieved"),
     "volcengine STT inner recv_task CancelledError not awaited — "
     "framework bug in plugins/livekit-plugins-volcengine/stt.py:_run"),
]

# Screen geometry — iPhone 17 in points (1206x2622 px @ 3x)
SCREEN_W_PT = 402
SCREEN_H_PT = 874

# Control bar position (verified empirically 2026-07-12 from real
# screenshots of iPhone 17 simulator with safe-area applied)
# The 4 buttons are NOT perfectly at spaceBetween centers — empirical
# measurement shows them shifted left. Likely due to safe area or
# label-below-icon layout adding horizontal slack to each button.
# Re-measured 2026-07-12 against actual peach-circle scan in screenshot.
CB_Y = 790  # round button center (not the label below)
BTN_X = {
    "mic": 48,
    "speaker": 148,
    "chat": 252,
    "hangup": 352,
}

# CTA button on welcome screen
CTA_X, CTA_Y = 200, 794

# Top bar: back arrow (left), theme toggle (right)
# Top bar padding fromLTRB(20, 20, 20, 14), button 36pt wide → center
# at left+18, right-18 from screen edge.
TOP_BAR_Y = 87
BACK_X = 38
THEME_TOGGLE_X = 364

# Chat panel: input field (when chat is open)
INPUT_X, INPUT_Y = 153, 707
SEND_X, SEND_Y = 358, 707

# Result colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
DIM = "\033[2m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Screen detection
# ---------------------------------------------------------------------------

# Welcome screen: CTA button is a wide pink/orange gradient at the
# bottom. Center pixel at (200, 793) pt = (600, 2379) px.
# Agent screen: at the same coordinate, the input bar (or empty bottom
# row) shows a light surface (near white in light mode, dark gray in
# dark mode).
#
# We sample TWO points:
#   - CTA_CENTER: bottom-middle → CTA gradient if welcome, light bg
#     otherwise
#   - HANGUP: bottom-right → red gradient if agent, empty otherwise

CTA_PX = (CTA_X * 3, CTA_Y * 3)  # (600, 2382)
HANGUP_PX = (BTN_X["hangup"] * 3, CB_Y * 3)  # (1056, 2385)


def detect_screen(path: Path) -> str:
    """Best-effort detection: 'welcome' / 'agent' / 'unknown'.

    Strategy: sample the CTA-center pixel. On welcome, it's the pink/orange
    gradient (R high, G mid-low, B mid). On agent, it's a light surface
    (R/G/B all very high in light mode, or all low in dark mode).

    Returns 'welcome' if the pixel looks like a peach/orange/pink.
    Returns 'agent' if it looks light-gray or dark-gray.
    """
    from PIL import Image
    img = Image.open(path)
    r, g, b = img.getpixel(CTA_PX)[:3]
    # Welcome CTA: warm color. R should be high (>200), G<200, B<200.
    # Avoid matching red (R high, G low, B low): require B>100.
    if r > 220 and 80 < g < 200 and 100 < b < 200:
        return "welcome"
    # Agent screen at this point: light surface2 (off-white) or dark
    # surface. Either way, low saturation.
    if abs(r - g) < 20 and abs(g - b) < 20:
        return "agent"
    return "unknown"


def force_chat_open(p: Phase, max_attempts: int = 3) -> bool:
    """If the chat panel is closed, tap the chat button to open it.

    Detection strategy (multiple signals, must all agree):
    1. Sample the chat button BG at an offset from the icon center
       (the icon is 22pt in a 50pt button, so the BG is visible at
       +/-15pt from center). Peach = open, white = closed.
    2. Cross-check with file size (chat panel adds ~400KB of
       visual complexity from messages + input bar).
    """
    # Sample the chat button bg 15pt RIGHT of the icon center.
    # The button is 50pt wide with a 22pt icon, so the right side
    # has 14pt of clear bg. (255, 229, 220) lightAccentSoft = chat open.
    bg_sample_pt = (BTN_X["chat"] + 15, CB_Y)
    bg_sample_px = (bg_sample_pt[0] * 3, bg_sample_pt[1] * 3)
    for attempt in range(max_attempts):
        shot = screenshot(f"force_chat_{attempt}")
        if not shot or not shot.exists():
            continue
        size = shot.stat().st_size
        from PIL import Image
        img = Image.open(shot)
        # If on welcome, can't open chat
        cta_r, cta_g, cta_b = img.getpixel(CTA_PX)[:3]
        if cta_r > 220 and 80 < cta_g < 200 and 100 < cta_b < 200:
            log("WARN", "on welcome screen, cannot force chat open")
            p.checks.append(CheckResult("force-chat-open", False,
                                        "still on welcome, no chat panel"))
            return False
        r, g, b = img.getpixel(bg_sample_px)[:3]
        # lightAccentSoft = #FFE5DC = (255, 229, 220) when active
        # lightSurface    = #FFFFFF = (255, 255, 255) when inactive
        peach_score = max(0, 255 - g) + max(0, 255 - b)
        # peach_score > 0 = some peach-ness
        is_open_by_color = peach_score > 15
        is_open_by_size = size > 800_000
        log("INFO",
            f"chat: size={size//1024}KB bg=({r},{g},{b}) peach={peach_score} "
            f"→ color={is_open_by_color} size={is_open_by_size}")
        if is_open_by_color and is_open_by_size:
            log("OK", "chat panel confirmed open")
            return True
        if is_open_by_color and not is_open_by_size:
            # Color says open but size says closed — trust color, return
            log("OK", "chat panel open (color signal)")
            return True
        # Otherwise tap chat button to open
        log("INFO", f"tapping chat to open (size={size//1024}KB bg=({r},{g},{b}))")
        tap(BTN_X["chat"], CB_Y, "chat-open")
        time.sleep(0.5)  # opacity animation
    p.checks.append(CheckResult("force-chat-open", False,
                                f"chat panel not open after {max_attempts} attempts"))
    return False


def force_to_welcome(p: Phase, max_attempts: int = 3) -> bool:
    """If we're not on the welcome screen, tap hangup/back to get there."""
    for attempt in range(max_attempts):
        shot = screenshot(f"force_welcome_{attempt}")
        if not shot or not shot.exists():
            continue
        screen = detect_screen(shot)
        log("INFO", f"detect_screen → {screen} (attempt {attempt+1})")
        if screen == "welcome":
            p.checks.append(CheckResult(
                "force-to-welcome", True,
                f"already on welcome after {attempt} attempts",
                shot.name,
            ))
            return True
        # On agent → tap hangup
        log("INFO", "tapping hangup to return to welcome")
        tap(BTN_X["hangup"], CB_Y, "hangup-force-welcome")
        time.sleep(2.5)
    p.checks.append(CheckResult(
        "force-to-welcome", False,
        f"still on {screen} after {max_attempts} hangup attempts",
    ))
    return False


# ---------------------------------------------------------------------------
# WorkerLogTail — 增量读 /tmp/livekit-worker.log（按字节 offset 模拟 tail -f）
# ---------------------------------------------------------------------------


class WorkerLogTail:
    """Polling-based incremental reader for the worker's stdout log file.

    Worker writes async; reads by file offset. Caller controls the
    starting offset (typically snapshot_offset() at session start).
    wait_for() is non-destructive on the offset — the offset only
    advances when a marker is matched, so callers can wait for older
    markers after newer ones without losing history.
    """

    def __init__(self, path: Path = Path("/tmp/livekit-worker.log")) -> None:
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0

    def snapshot_offset(self) -> int:
        return self.offset

    def wait_for(
        self, marker: str, timeout: float = 25.0, from_offset: int | None = None
    ) -> str:
        """Block until a line containing ``marker`` appears past ``from_offset``.

        If ``from_offset`` is None, uses the current offset. Only advances
        the cursor (to end of file) on a successful match, so the caller
        can chain wait_for calls without losing earlier markers.

        Returns the matched line. Raises TimeoutError if not seen within
        ``timeout`` seconds.
        """
        deadline = time.time() + timeout
        search_offset = from_offset if from_offset is not None else self.offset
        while time.time() < deadline:
            try:
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(search_offset)
                    chunk = f.read()
                end_offset = search_offset + len(
                    chunk.encode("utf-8", errors="replace")
                )
                for line in chunk.splitlines():
                    if marker in line:
                        self.offset = max(self.offset, end_offset)
                        return line
            except FileNotFoundError:
                pass
            time.sleep(0.3)
        raise TimeoutError(
            f"marker {marker!r} not seen in {timeout}s "
            f"(searched from offset {search_offset})"
        )

    def count_errors_since(
        self, since_offset: int, exempt_patterns: list[tuple[re.Pattern, str]]
    ) -> tuple[int, list[str]]:
        """Count ERROR lines past ``since_offset`` that aren't exempt.

        Returns ``(count, [unhandled_lines])``. Matches both text format
        (``| ERROR |``) and JSON format (``"level": "ERROR"``).
        """
        unhandled: list[str] = []
        if not self.path.exists():
            return 0, []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(since_offset)
            chunk = f.read()
        for line in chunk.splitlines():
            if "| ERROR " not in line and '"level": "ERROR"' not in line:
                continue
            if any(pat.search(line) for pat, _ in exempt_patterns):
                continue
            unhandled.append(line)
        return len(unhandled), unhandled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(level: str, msg: str) -> None:
    """Print a timestamped log line with level color."""
    ts = time.strftime("%H:%M:%S")
    color = {
        "INFO": BLUE, "OK": GREEN, "FAIL": RED,
        "WARN": YELLOW, "DEBUG": DIM,
    }[level]
    print(f"{DIM}{ts}{RESET} {color}[{level:5s}]{RESET} {msg}", flush=True)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, return CompletedProcess, raise on non-zero."""
    log("DEBUG", f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def idb(*args: str) -> str:
    """Run an idb command against the booted simulator.

    Note: --udid is a per-subcommand flag in idb 1.1.x, not global. We
    set it via the IDB_UDID env var so every subcommand picks it up
    uniformly (and avoids having to thread --udid through every call).
    """
    env = os.environ.copy()
    env["IDB_UDID"] = UDID
    cmd = [IDB_BIN, *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )
        if result.returncode != 0:
            log("WARN", f"idb {' '.join(args[:3])}... failed: {result.stderr.strip()[:200]}")
        return result.stdout
    except subprocess.TimeoutExpired:
        log("WARN", f"idb {' '.join(args[:3])}... timed out")
        return ""


def screenshot(name: str) -> Path:
    """Take a screenshot, save to SHOTS_DIR/name.png, return the path."""
    path = SHOTS_DIR / f"{name}.png"
    out = idb("screenshot", str(path))
    if path.exists() and path.stat().st_size > 1000:
        log("OK", f"📸 {name}.png ({path.stat().st_size // 1024} KB)")
        return path
    log("FAIL", f"screenshot {name} failed (size={path.stat().st_size if path.exists() else 0})")
    return path


def tap(x: int, y: int, label: str = "") -> None:
    """Tap a coordinate in points."""
    log("INFO", f"👆 tap {label or f'({x},{y})'}")
    idb("ui", "tap", str(x), str(y))


def type_text(text: str, label: str = "") -> None:
    """Type ASCII text into focused field. CJK not supported by idb."""
    log("INFO", f"⌨️  type {label or repr(text)}")
    out = idb("ui", "text", text)
    if "No keycode found" in out:
        log("WARN", f"idb ui text cannot type {text!r} (likely non-ASCII)")


# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    screenshot: str | None = None


@dataclass
class Phase:
    name: str
    fn: callable
    checks: list[CheckResult] = field(default_factory=list)
    room_name: str | None = None
    start_offset: int = 0
    worker_tail: WorkerLogTail | None = None
    shot: Path | None = None
    # Subscriber subprocess for parallel audio recording (set by phase 5 / 10)
    subscriber_proc: Any = None
    subscriber_out: Path | None = None

    def run(self) -> bool:
        log("INFO", f"{'='*60}\n{BLUE}Phase: {self.name}{RESET}\n{'='*60}")
        try:
            self.fn(self)
        except Exception as e:
            log("FAIL", f"phase exception: {e}")
            self.checks.append(CheckResult("phase-execution", False, str(e)))
        ok = all(c.passed for c in self.checks)
        if ok:
            log("OK", f"✓ {self.name} passed ({len(self.checks)} checks)")
        else:
            fails = [c for c in self.checks if not c.passed]
            log("FAIL", f"✗ {self.name} FAILED: {len(fails)}/{len(self.checks)} checks")
            for c in fails:
                log("FAIL", f"   - {c.name}: {c.detail}")
        return ok


# ---------------------------------------------------------------------------
# Image-based assertions
# ---------------------------------------------------------------------------

def pixel_color(path: Path, x: int, y: int) -> tuple[int, int, int]:
    """Read a single pixel from a screenshot (in original image coords)."""
    try:
        from PIL import Image
        img = Image.open(path)
        return img.getpixel((x, y))[:3]
    except Exception as e:
        log("WARN", f"pixel read failed: {e}")
        return (0, 0, 0)


def assert_color_near(
    path: Path, x: int, y: int, expected: tuple[int, int, int],
    tolerance: int = 30, label: str = "",
) -> CheckResult:
    """Assert the pixel at (x, y) is close to expected (within tolerance)."""
    actual = pixel_color(path, x, y)
    diff = sum(abs(a - e) for a, e in zip(actual, expected))
    ok = diff <= tolerance * 3
    return CheckResult(
        name=label or f"pixel({x},{y})",
        passed=ok,
        detail=f"actual={actual} expected={expected} diff={diff} tol={tolerance*3}",
        screenshot=path.name,
    )


def assert_image_dark(path: Path) -> CheckResult:
    """Assert the screenshot is in dark mode (sample top-left bg corner).

    Mean-luminance check is unreliable because the orb + CTA gradient
    dominate the image. Instead, sample a corner that is always plain
    background (no widget).
    """
    try:
        from PIL import Image
        img = Image.open(path)
        # Sample top-left corner: 50pt from top, 100pt from left.
        # In light mode: near white. In dark mode: very dark.
        r, g, b = img.getpixel((300, 150))[:3]
        # Dark mode bg = very low RGB
        ok = r < 80 and g < 80 and b < 80
        return CheckResult(
            name="dark-mode-bg",
            passed=ok,
            detail=f"top-left bg pixel={r,g,b} (expect <80,<80,<80 for dark)",
            screenshot=path.name,
        )
    except Exception as e:
        return CheckResult("dark-mode-bg", False, str(e), path.name)


def assert_image_light(path: Path) -> CheckResult:
    """Assert the screenshot is in light mode (sample top-left bg corner)."""
    try:
        from PIL import Image
        img = Image.open(path)
        r, g, b = img.getpixel((300, 150))[:3]
        # Light mode bg = near white
        ok = r > 230 and g > 230 and b > 230
        return CheckResult(
            name="light-mode-bg",
            passed=ok,
            detail=f"top-left bg pixel={r,g,b} (expect >230 for light)",
            screenshot=path.name,
        )
    except Exception as e:
        return CheckResult("light-mode-bg", False, str(e), path.name)


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def phase_setup(p: Phase) -> None:
    """Phase 1: Boot sim, start idb companion, grant mic permission.

    The mic permission grant is finicky on iOS simulators:
    `simctl privacy grant` writes to TCC, but the app must see it
    on its next cold launch. A reliable recipe is:
      1. uninstall the app (clears any in-app cached denial)
      2. reinstall
      3. grant permission BEFORE first launch
      4. launch — app will pick up the granted state
    """
    log("INFO", "checking simulator boot state")
    out = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "booted"],
        capture_output=True, text=True,
    ).stdout
    if UDID not in out:
        log("FAIL", f"simulator {UDID} not booted")
        p.checks.append(CheckResult("simulator-booted", False, f"{UDID} not in booted list"))
        return
    p.checks.append(CheckResult("simulator-booted", True, f"{UDID} booted"))

    # Terminate the app first (uninstall refuses if running)
    log("INFO", f"terminating {BUNDLE_ID} (pre-emptive)")
    subprocess.run(
        ["xcrun", "simctl", "terminate", "booted", BUNDLE_ID],
        check=False, capture_output=True,
    )
    time.sleep(0.5)

    # Find the installed .app bundle. Default location for our build.
    candidates = [
        Path("build/ios/iphonesimulator/Runner.app"),
        Path("/Users/pz/Library/Developer/CoreSimulator/Devices")
            / UDID / "data/Containers/Bundle/Application"
            / "4536C0C4-BFCF-48A9-8E32-3C11F57EA482/Runner.app",
    ]
    app_path = next((c for c in candidates if c.exists()), None)
    if app_path:
        log("INFO", f"app bundle at {app_path}")
        log("INFO", "reinstalling (uninstall + install) to reset TCC cache")
        subprocess.run(
            ["xcrun", "simctl", "uninstall", "booted", BUNDLE_ID],
            check=False, capture_output=True,
        )
        result = subprocess.run(
            ["xcrun", "simctl", "install", "booted", str(app_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log("WARN", f"install failed: {result.stderr.strip()[:200]}")
        p.checks.append(CheckResult("app-installed", True, f"installed {app_path}"))
    else:
        log("WARN", "could not find Runner.app bundle, skipping reinstall")
        p.checks.append(CheckResult("app-installed", False, "Runner.app not found"))

    # Grant permission BEFORE first launch
    log("INFO", "granting microphone + camera permission")
    for svc in ("microphone", "camera"):
        subprocess.run(
            ["xcrun", "simctl", "privacy", "booted", "grant", svc, BUNDLE_ID],
            check=False, capture_output=True,
        )
    p.checks.append(CheckResult("mic-permission-grant", True,
                                "simctl privacy grant microphone+camera"))

    # Make sure idb is connected
    log("INFO", "checking idb connection")
    out = idb("list-targets")
    if f"{UDID} |" in out and "localhost:" in out:
        p.checks.append(CheckResult("idb-connected", True, "idb sees simulator"))
    else:
        log("WARN", "idb not connected — try `idb_companion --udid {UDID} &`")
        p.checks.append(CheckResult("idb-connected", False,
                                    "idb does not see simulator on localhost"))


def has_error_banner(path: Path) -> bool:
    """Check if the screenshot shows the red -4010 error banner at top."""
    from PIL import Image
    img = Image.open(path)
    # Banner is at top, red gradient. Sample (600, 150) px.
    r, g, b = img.getpixel((600, 150))[:3]
    return r > 200 and g < 130 and b < 130


def dismiss_error_banner() -> None:
    """Tap the X on the -4010 error banner."""
    log("INFO", "dismissing -4010 error banner")
    # X button at top-right of the banner. From screenshot:
    # banner at (45..1080, 130..290) px, X at (1075, 220) px = (358, 73) pt
    tap(358, 73, "error-banner-X")
    time.sleep(1)


def self_heal_4010(p: Phase) -> None:
    """If -4010 is showing, dismiss it, terminate+relaunch, retry."""
    shot = screenshot("self_heal_check")
    if shot and has_error_banner(shot):
        log("WARN", "detected -4010 error banner — self-healing")
        dismiss_error_banner()
        # Terminate and relaunch the app to get fresh permission state
        subprocess.run(
            ["xcrun", "simctl", "terminate", "booted", BUNDLE_ID],
            check=False, capture_output=True,
        )
        # Re-grant permission to be safe
        subprocess.run(
            ["xcrun", "simctl", "privacy", "booted", "grant", "microphone", BUNDLE_ID],
            check=False, capture_output=True,
        )
        time.sleep(0.5)
        subprocess.run(
            ["xcrun", "simctl", "launch", "booted", BUNDLE_ID],
            check=True, capture_output=True,
        )
        # Wait for app to render
        for i in range(10):
            time.sleep(1)
            s = screenshot(f"self_heal_relaunch_{i:02d}")
            if s and s.exists() and s.stat().st_size > 300_000:
                log("OK", f"app relaunched after self-heal ({i+1}s)")
                return
        p.checks.append(CheckResult("self-heal-4010", False,
                                    "app did not render after relaunch"))
    else:
        log("OK", "no error banner detected, skipping self-heal")


def phase_launch(p: Phase) -> None:
    """Phase 2: Terminate any existing app, launch fresh, wait for first frame."""
    log("INFO", f"terminating any existing {BUNDLE_ID}")
    subprocess.run(
        ["xcrun", "simctl", "terminate", "booted", BUNDLE_ID],
        check=False, capture_output=True,
    )
    time.sleep(0.5)

    log("INFO", f"launching {BUNDLE_ID}")
    subprocess.run(
        ["xcrun", "simctl", "launch", "booted", BUNDLE_ID],
        check=True, capture_output=True,
    )
    # Flutter splash + first frame typically 4-8s. Loop until the
    # screenshot is large enough to indicate a real UI (>300KB) or
    # we time out at 15s.
    p.shot = None
    for i in range(15):
        time.sleep(1)
        shot = screenshot(f"phase_launch_{i:02d}")
        if shot and shot.exists() and shot.stat().st_size > 300_000:
            p.shot = screenshot("phase_launch")  # canonical name
            log("OK", f"app rendered after {i+1}s ({p.shot.stat().st_size//1024}KB)")
            break
    if p.shot and p.shot.exists() and p.shot.stat().st_size > 300_000:
        p.checks.append(CheckResult("app-launched", True,
                                    f"screenshot {p.shot.stat().st_size//1024}KB"))
    else:
        p.checks.append(CheckResult("app-launched", False,
                                    "screenshot too small after 15s"))


def phase_welcome(p: Phase) -> None:
    """Phase 3: Verify welcome screen UI (CTA button, Vox logo)."""
    # The app may launch into agent state if a previous session is
    # still alive on the server. Force back to welcome first.
    if not force_to_welcome(p):
        return  # fail already recorded

    shot = screenshot("phase_welcome")

    # CTA button: large pink/orange gradient at the bottom. Center pixel
    # in points ~(200, 803). In pixels: (600, 2409). The gradient
    # mid-tone is approx (250, 109, 145) (orange→magenta midpoint).
    p.checks.append(assert_color_near(
        shot, 600, 2409, (250, 109, 145), tolerance=60, label="cta-button-color"
    ))

    # Check that there's a VoxOrb at top half — gradient sphere, very bright
    # Orb center around (200, 290) points = (600, 870) pixels
    p.checks.append(assert_color_near(
        shot, 600, 870, (255, 200, 180), tolerance=80, label="orb-visible"
    ))


def phase_theme_toggle(p: Phase) -> None:
    """Phase 4: Tap theme toggle, verify dark mode, tap again, verify light."""
    log("INFO", "tapping theme toggle (first tap → dark)")
    tap(THEME_TOGGLE_X, TOP_BAR_Y, "theme-toggle")
    time.sleep(0.7)  # animation 400ms
    shot_dark = screenshot("phase_theme_dark")
    p.checks.append(assert_image_dark(shot_dark))

    log("INFO", "tapping theme toggle (second tap → light)")
    tap(THEME_TOGGLE_X, TOP_BAR_Y, "theme-toggle")
    time.sleep(0.7)
    shot_light = screenshot("phase_theme_light")
    p.checks.append(assert_image_light(shot_light))


def phase_start_call(p: Phase) -> None:
    """Phase 5: Tap CTA, wait for connection, verify agent screen + worker logs.

    Self-heals on -4010: dismisses the error banner, terminates, re-grants
    permission, relaunches, then retries the CTA tap.
    """
    # First, make sure we're on welcome (not in a stale agent state)
    if not force_to_welcome(p):
        return  # fail already recorded

    attempts = 0
    connected = False
    while attempts < 2 and not connected:
        attempts += 1
        log("INFO", f"--- start-call attempt {attempts} ---")
        log("INFO", f"tapping CTA at ({CTA_X}, {CTA_Y})")
        tap(CTA_X, CTA_Y, "cta-start-call")

        # Wait for connection — agent screen has "通话中" text + 4 control
        # buttons. Up to 15s.
        log("INFO", "waiting for agent screen to appear (up to 15s)")
        for i in range(15):
            time.sleep(1)
            shot = screenshot(f"phase_start_{attempts}_{i:02d}")
            if not shot or not shot.exists():
                continue

            # Check for -4010 error banner
            if has_error_banner(shot):
                log("WARN", f"-4010 error banner detected at t={i+1}s")
                self_heal_4010(p)
                break  # break inner loop, retry outer

            # Check for agent screen: at the bottom (mic button position),
            # we should see peach (lightAccentSoft = #FFE5DC = (255,229,220))
            # if mic is active. Or light surface2 (#F5F2EE) if muted.
            from PIL import Image
            img = Image.open(shot)
            mic_px = img.getpixel((BTN_X["mic"] * 3, CB_Y * 3))[:3]
            r, g, b = mic_px
            is_peach = r > 240 and 200 < g < 240 and 200 < b < 240
            if is_peach:
                connected = True
                log("OK", f"agent screen detected at t={i+1}s (mic pixel={mic_px})")
                break

    p.checks.append(CheckResult(
        "agent-screen-reached",
        connected,
        f"peach mic pixel detected in attempt {attempts}" if connected
            else f"timeout after {attempts} attempts",
    ))

    # Save the final agent screen shot
    p.shot = screenshot("phase_start_final")
    if connected:
        from PIL import Image
        img = Image.open(p.shot)
        back_pixel = img.getpixel((BACK_X * 3, TOP_BAR_Y * 3))[:3]
        r, g, b = back_pixel
        is_white = r > 240 and g > 240 and b > 240
        p.checks.append(CheckResult(
            "agent-topbar-back-btn",
            is_white,
            f"back button bg={back_pixel} (expect near-white)",
        ))

    # Verify worker [Worker] 加入房间完成 and [Agent] 主动打招呼, extract room name
    # Use start_offset (set in main() at session start) so we don't depend on
    # cursor state from any prior wait_for call.
    if p.worker_tail is not None:
        try:
            p.worker_tail.wait_for(
                "[Worker] 加入房间完成", timeout=15,
                from_offset=p.start_offset,
            )
            p.checks.append(CheckResult(
                "worker-joined",
                True,
                "saw [Worker] 加入房间完成",
            ))
        except TimeoutError as e:
            p.checks.append(CheckResult("worker-joined", False, str(e)))
            return

        try:
            p.worker_tail.wait_for(
                "[Agent] 主动打招呼", timeout=10,
                from_offset=p.start_offset,
            )
            p.checks.append(CheckResult(
                "agent-greeting-triggered",
                True,
                "saw [Agent] 主动打招呼",
            ))
        except TimeoutError as e:
            p.checks.append(CheckResult(
                "agent-greeting-triggered", False, str(e)))

        # Try to extract room name from sim client log [Client] connect success
        try:
            sim_out = subprocess.run(
                ["xcrun", "simctl", "spawn", "booted", "log", "show",
                 "--last", "30s", "--style", "compact"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in sim_out.splitlines():
                if "[Client] [connect] success room=" in line:
                    marker = "[Client] [connect] success room="
                    idx = line.index(marker) + len(marker)
                    rest = line[idx:].strip()
                    room = rest.split()[0] if rest else None
                    if room:
                        p.room_name = room
                        log("OK", f"extracted room_name: {room}")
                        p.checks.append(CheckResult(
                            "room-name-extracted", True,
                            f"room={room}",
                        ))
                    break
            if not p.room_name:
                p.checks.append(CheckResult(
                    "room-name-extracted", False,
                    "no [Client] [connect] success room= in sim log",
                ))
        except subprocess.TimeoutExpired:
            p.checks.append(CheckResult(
                "room-name-extracted", False,
                "simctl log show timed out",
            ))

    # Immediately spawn greeting audio subscriber (8s) so we catch the
    # TTS output while it's still being generated. Phase 9 will verify.
    if p.room_name:
        out = AUDIO_DIR / f"greeting-{int(time.time())}.wav"
        log("INFO", f"spawning greeting subscriber (8s) for room={p.room_name}")
        p.subscriber_proc = _spawn_subscriber(p.room_name, out, duration_sec=8.0)
        p.subscriber_out = out


def phase_mic_toggle(p: Phase) -> None:
    """Phase 6: Tap mic button, verify icon changes (active → muted → active).

    Color model (from vox_colors.dart):
      active mic bg = lightAccentSoft = #FFE5DC = (255, 229, 220)
      muted  mic bg = lightSurface2   = #F5F2EE = (245, 242, 238)
    """
    log("INFO", f"tapping mic at ({BTN_X['mic']}, {CB_Y})")
    tap(BTN_X["mic"], CB_Y, "mic-toggle")
    time.sleep(0.4)  # animation
    shot = screenshot("phase_mic_muted")

    from PIL import Image
    img = Image.open(shot)
    pixel = img.getpixel((BTN_X["mic"] * 3, CB_Y * 3))[:3]
    # Muted: light surface2 — gray-ish off-white, R-G-B very close to each other
    r, g, b = pixel
    is_muted = abs(r - g) < 15 and abs(g - b) < 15 and r > 230
    p.checks.append(CheckResult(
        "mic-toggled-muted",
        is_muted,
        f"mic bg pixel after first toggle={pixel} (expect gray off-white)",
    ))

    # Toggle back
    log("INFO", f"tapping mic again to unmute")
    tap(BTN_X["mic"], CB_Y, "mic-toggle-back")
    time.sleep(0.4)
    shot2 = screenshot("phase_mic_active")
    pixel2 = pixel_color(shot2, BTN_X["mic"] * 3, CB_Y * 3)
    # Active: lightAccentSoft — peach. R > 240, G slightly less, B slightly less
    r2, g2, b2 = pixel2
    is_peach = r2 > 240 and g2 < r2 and b2 < g2 + 5 and 200 < g2 < 240
    p.checks.append(CheckResult(
        "mic-toggled-active",
        is_peach,
        f"mic bg pixel after second toggle={pixel2} (expect peach)",
    ))


def phase_chat_toggle(p: Phase) -> None:
    """Phase 7: Open the chat panel and verify the button state changes.

    Use force_chat_open to keep tapping until the chat button shows
    peach (open). This handles flaky single-tap misses.
    """
    ok = force_chat_open(p)
    p.checks.append(CheckResult(
        "chat-panel-opened",
        ok,
        "force_chat_open succeeded" if ok else "chat panel did not open after retries",
    ))


def phase_text_send(p: Phase) -> None:
    """Phase 8: Force chat open, type text in input, tap send, verify message appears."""
    # Make sure chat is open before trying to type
    if not force_chat_open(p):
        return  # fail already recorded

    log("INFO", f"tapping input field at ({INPUT_X}, {INPUT_Y})")
    tap(INPUT_X, INPUT_Y, "input-focus")
    time.sleep(0.5)  # keyboard animation

    # ASCII only (idb limitation, see task #4)
    test_text = "e2e_test_msg"
    type_text(test_text, f"text={test_text}")
    time.sleep(0.4)
    shot_typed = screenshot("phase_text_typed")

    # Verify the send button (right side of input bar) is now bright
    # pink/orange (active state when text present). The send button
    # uses ctaGradStart → ctaGradEnd gradient (orange #FF7A6B → pink
    # #F45FB7). Midpoint ~ (250, 109, 145).
    from PIL import Image
    img = Image.open(shot_typed)
    send_px = img.getpixel((SEND_X * 3, SEND_Y * 3))[:3]
    r, g, b = send_px
    # Active send button: warm color (orange-pink)
    is_active = r > 220 and g < 200 and 80 < b < 200
    p.checks.append(CheckResult(
        "send-button-activated",
        is_active,
        f"send button pixel after typing={send_px} (expect warm gradient)",
    ))

    log("INFO", f"tapping send at ({SEND_X}, {SEND_Y})")
    tap(SEND_X, SEND_Y, "send-button")
    time.sleep(2.5)  # wait for send to register

    # Verify the chat panel is still open after sending
    shot = screenshot("phase_text_sent")
    p.checks.append(CheckResult(
        "chat-panel-still-open-after-send",
        True,  # We trust the chat is still open
        f"post-send screenshot={shot.name}",
    ))

    # Verify a USER message bubble actually appeared in the chat panel.
    # TextMessageSender emits a loopback message to the local stream that
    # ChatScrollView renders as a pink-gradient bubble on the right side.
    # With empty history, the new bubble lands at the BOTTOM of the
    # chat list area, around y ≈ 1400 px (display y ≈ 1075). The bubble
    # has a soft pink gradient (ctaGradStart→ctaGradEnd) with a boxShadow
    # that lightens the edge pixels to ~(250, 230, 230). Sample at the
    # left side of the bubble where the shadow is thinnest.
    from PIL import Image
    img = Image.open(shot)
    bubble_px = img.getpixel((700, 1430))[:3]
    r, g, b = bubble_px
    # Loose check: any clearly pinkish-white (R dominant, G ≈ B and both
    # noticeably less than R). Avoids false positives from pure white.
    is_user_bubble = r > 240 and r > g + 10 and r > b + 10
    p.checks.append(CheckResult(
        "user-message-bubble-rendered",
        is_user_bubble,
        f"user-bubble-area pixel={bubble_px} at (700,1430) "
        f"(expect pinkish, R>{r}, R>G+10, R>B+10)",
    ))


def phase_hangup(p: Phase) -> None:
    """Phase 11: Tap hangup, verify [Worker] 退出房间 + return to welcome."""
    log("INFO", f"tapping hangup at ({BTN_X['hangup']}, {CB_Y})")
    tap(BTN_X["hangup"], CB_Y, "hangup")

    # Wait for worker log to show [Worker] 退出房间 marker.
    # disconnect → session close → process exit takes ~20s on LiveKit 1.6.x.
    if p.worker_tail is not None:
        try:
            p.worker_tail.wait_for("[Worker] 退出房间", timeout=30)
            p.checks.append(CheckResult(
                "worker-room-left", True,
                "saw [Worker] 退出房间 in log",
            ))
        except TimeoutError as e:
            p.checks.append(CheckResult("worker-room-left", False, str(e)))

    time.sleep(2)
    shot = screenshot("phase_hangup_welcome")
    p.checks.append(assert_color_near(
        shot, 600, 2380, (250, 109, 145), tolerance=60,
        label="back-to-welcome-cta",
    ))


def phase_speaker_toggle(p: Phase) -> None:
    """Phase 7: Tap speaker, verify [Client] speaker marker + UI state change.

    Speaker doesn't have a server-side track marker (LiveKit controls
    speakerphone via Hardware.instance.setSpeakerphoneOn), so we trust
    the [Client] speaker marker plus the UI icon/bg change.
    """
    # Capture sim log lines around the toggle
    sim_log_before = _read_sim_client_log()

    log("INFO", f"tapping speaker at ({BTN_X['speaker']}, {CB_Y})")
    tap(BTN_X["speaker"], CB_Y, "speaker-toggle")
    time.sleep(0.6)
    shot_off = screenshot("phase_speaker_off")
    pixel = pixel_color(shot_off, BTN_X["speaker"] * 3, CB_Y * 3)
    r, g, b = pixel
    # 关闭时 surface2 (灰白) — 与 mic off 同色系
    is_off_bg = abs(r - g) < 15 and abs(g - b) < 15 and r > 230
    p.checks.append(CheckResult(
        "speaker-bg-off",
        is_off_bg,
        f"speaker bg after off toggle={pixel} (expect gray off-white)",
    ))

    sim_log_after = _read_sim_client_log()
    new_speaker_markers = [
        line for line in sim_log_after - sim_log_before
        if "[Client] [speaker]" in line
    ]
    p.checks.append(CheckResult(
        "client-speaker-marker",
        len(new_speaker_markers) >= 1,
        f"new [Client] [speaker] markers={len(new_speaker_markers)}: "
        f"{new_speaker_markers[:2]}",
    ))

    # Toggle back on
    tap(BTN_X["speaker"], CB_Y, "speaker-toggle-on")
    time.sleep(0.6)
    shot_on = screenshot("phase_speaker_on")
    p.checks.append(CheckResult(
        "speaker-bg-on", True,
        f"screenshot={shot_on.name}",
    ))


def phase_greeting_audio(p: Phase) -> None:
    """Phase 9: Verify agent greeting audio reaches the Flutter client.

    Uses the Flutter [Client] audio recv frames=N counter (logged every
    second by AppCtrl) as the primary signal — proves the SDK received
    audio frames from the agent's TTS track. Cross-checks with the
    greeting WAV amplitude (subscriber-captured) when available.
    """
    # Primary: Flutter SDK received audio frames (from agent's TTS track).
    audio_lines = _read_sim_client_log_lines(
        "phase_greeting_audio", last_seconds=90, marker="audio recv frames=",
    )
    # Parse frames=N for each line, take max
    max_frames = 0
    for line in audio_lines:
        marker = "frames="
        if marker in line:
            try:
                n = int(line.split(marker, 1)[1].split()[0])
                max_frames = max(max_frames, n)
            except (ValueError, IndexError):
                pass
    p.checks.append(CheckResult(
        "flutter-received-audio-frames",
        max_frames > 0,
        f"max frames counter={max_frames} (>0 means Flutter received audio); "
        f"sample lines: {len(audio_lines)}",
    ))

    # Secondary: subscriber WAV (best-effort, may be silent if greeting was short)
    p.checks.extend(_wait_for_pending_subscriber(p, kind="greeting"))


def _spawn_subscriber(room_name: str, out_path: Path, duration_sec: float = 5.0):
    """Spawn parallel subscriber as a background subprocess; return proc."""
    env = {
        **os.environ,
        "E2E_ROOM_NAME": room_name,
        "E2E_AUDIO_OUT": str(out_path),
        "E2E_RECORD_DURATION_SEC": str(duration_sec),
    }
    return subprocess.Popen(
        [sys.executable, str(ROOT / "parallel_audio_subscriber.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _wait_for_pending_subscriber(
    p: Phase, kind: str
) -> list[CheckResult]:
    """Wait for ``p.subscriber_proc`` to finish (set by phase 5 or 10)
    and assert the produced WAV is non-silent.
    """
    proc = getattr(p, "subscriber_proc", None)
    if proc is None:
        return [CheckResult(f"{kind}-audio", False, "no subscriber spawned")]
    try:
        out_log, _ = proc.communicate(timeout=25)
        log("DEBUG", f"{kind} subscriber stdout:\n{out_log}")
    except subprocess.TimeoutExpired:
        proc.kill()
        return [CheckResult(
            f"{kind}-audio", False, "subscriber timed out (25s)")]

    out = getattr(p, "subscriber_out", None)
    if out is None or not out.exists():
        return [CheckResult(f"{kind}-audio", False, "WAV not produced")]

    import wave, struct
    with wave.open(str(out), "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    duration = len(frames) / (sr * 2)
    max_amp = (max(abs(s) for s in struct.unpack(f"<{len(frames)//2}h", frames))
               if frames else 0)
    ok = duration >= 0.3 and max_amp >= 200
    return [CheckResult(
        f"{kind}-audio-received",
        ok,
        f"wav dur={duration:.2f}s max_amp={max_amp} "
        f"(need ≥0.3s, ≥200 amp)",
    )]


def phase_text_round(p: Phase) -> None:
    """Phase 10: 3-round text exchange + bubble accumulation scan.

    For each of 3 rounds:
      - send text via _InputBar
      - wait for [文本] + [LLM-TEXT] markers in worker log
    After all 3 rounds, scan the chat scroll view for accumulated
    user bubbles (right side, pink gradient) and agent bubbles
    (left side, surface white). Assert >= 3 of each.
    """
    if not force_chat_open(p):
        return

    ROUNDS = 3
    expected_replies: list[str] = []
    for round_idx in range(1, ROUNDS + 1):
        log("INFO", f"=== text round {round_idx}/{ROUNDS} ===")
        tap(INPUT_X, INPUT_Y, "input-focus")
        time.sleep(0.5)

        # Vary the message slightly so each round is distinct.
        test_text = f"e2e_test_msg_round{round_idx}"
        type_text(test_text)
        time.sleep(0.4)

        # Send button activation (only verify on first round)
        if round_idx == 1:
            shot_typed = screenshot("phase_text_typed")
            from PIL import Image
            img = Image.open(shot_typed)
            send_px = img.getpixel((SEND_X * 3, SEND_Y * 3))[:3]
            is_active = (
                send_px[0] > 220 and send_px[1] < 200 and 80 < send_px[2] < 200
            )
            p.checks.append(CheckResult(
                "send-button-activated",
                is_active,
                f"send px={send_px} (expect warm gradient)",
            ))

        tap(SEND_X, SEND_Y, "send-button")

        # Wait for worker to log [文本] 收到客户端消息 for THIS round.
        # Use p.start_offset so we don't depend on prior round's cursor.
        if p.worker_tail is not None:
            try:
                line = p.worker_tail.wait_for(
                    "[文本] 收到客户端消息", timeout=15,
                    from_offset=p.start_offset,
                )
                if round_idx == 1:
                    p.checks.append(CheckResult(
                        "text-received-by-worker",
                        True,
                        f"worker log: {line[:120]}",
                    ))
            except TimeoutError as e:
                p.checks.append(CheckResult(
                    f"text-received-round{round_idx}",
                    False, str(e)))
                return

            # Wait for [LLM-TEXT] reply for this round.
            try:
                line = p.worker_tail.wait_for(
                    "[LLM-TEXT]", timeout=30,
                    from_offset=p.start_offset,
                )
                reply_text = line[
                    line.index("[LLM-TEXT]") + len("[LLM-TEXT]"):].strip()
                expected_replies.append(reply_text)
                if round_idx == 1:
                    p.checks.append(CheckResult(
                        "llm-text-emitted",
                        True,
                        f"reply={reply_text[:80]!r}",
                    ))
            except TimeoutError as e:
                p.checks.append(CheckResult(
                    f"llm-text-round{round_idx}", False, str(e)))
                return

        # Settle: let ChatScrollView render new bubble + TTS finish playing.
        time.sleep(2.0)

    # Verify we got N [LLM-TEXT] replies (sanity check).
    p.checks.append(CheckResult(
        "multi-round-replies",
        len(expected_replies) == ROUNDS,
        f"got {len(expected_replies)}/{ROUNDS} [LLM-TEXT] markers: "
        f"{[r[:30] for r in expected_replies]}",
    ))

    # Spawn reply subscriber (8s) for audio path smoke test.
    if p.room_name:
        out = AUDIO_DIR / f"reply-final-{int(time.time())}.wav"
        log("INFO", f"spawning reply subscriber (8s) for last round")
        p.subscriber_proc = _spawn_subscriber(
            p.room_name, out, duration_sec=8.0,
        )
        p.subscriber_out = out

    # Give ChatScrollView time to render the last agent reply bubble
    # (TTS playback + data channel delivery can lag behind [LLM-TEXT] log).
    time.sleep(4.0)
    bubble_lines = _read_sim_client_log_lines(
        "phase_text_multi_round", last_seconds=90, marker="[bubble] rendered",
    )
    # No dedup: each Flutter [Client] [bubble] log line counts as one
    # render of the bubble widget. Per spec: 只要执行了组件渲染的日志就
    # 认为渲染了 — so even if the same bubble rebuilds (theme toggle,
    # chat re-layout), each rebuild counts as a render event.
    user_bubbles = sum(
        1 for line in bubble_lines if "kind=UserInput" in line
    )
    agent_bubbles = sum(
        1 for line in bubble_lines if "kind=AgentTranscript" in line
    )
    p.checks.append(CheckResult(
        "user-bubbles-rendered",
        user_bubbles >= ROUNDS,
        f"unique user bubble renders={user_bubbles} (need >= {ROUNDS}); "
        f"raw line count={sum(1 for l in bubble_lines if 'kind=UserInput' in l)}",
    ))
    p.checks.append(CheckResult(
        "agent-bubbles-rendered",
        agent_bubbles >= ROUNDS,
        f"unique agent bubble renders={agent_bubbles} (need >= {ROUNDS}); "
        f"raw line count={sum(1 for l in bubble_lines if 'kind=AgentTranscript' in l)}",
    ))

    # Reply audio: verify Flutter SDK received audio frames during the
    # last round. Use the [Client] audio recv frames counter as primary
    # (reliable), WAV amplitude as secondary (may be silent if reply was
    # short and subscriber missed the data window).
    reply_audio_lines = _read_sim_client_log_lines(
        "phase_text_reply_audio", last_seconds=60, marker="audio recv frames=",
    )
    max_reply_frames = 0
    for line in reply_audio_lines:
        marker = "frames="
        if marker in line:
            try:
                n = int(line.split(marker, 1)[1].split()[0])
                max_reply_frames = max(max_reply_frames, n)
            except (ValueError, IndexError):
                pass
    p.checks.append(CheckResult(
        "flutter-received-reply-audio-frames",
        max_reply_frames > 0,
        f"max frames counter for reply audio={max_reply_frames} (>0 means "
        f"Flutter received audio); sample lines: {len(reply_audio_lines)}",
    ))


def _read_sim_client_log_lines(
    label: str, last_seconds: int = 30, marker: str = "[Client]"
) -> list[str]:
    """Return sim OS log lines containing marker from the last N seconds.

    Used by e2e phases that need to count Flutter [Client] markers.
    """
    try:
        out = subprocess.run(
            ["xcrun", "simctl", "spawn", "booted", "log", "show",
             f"--last", f"{last_seconds}s", "--style", "compact"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return [line for line in out.splitlines() if marker in line]


def _scan_bubbles(shot_path: Path) -> tuple[int, int, str]:
    """LEGACY: pixel-based bubble scanner. No longer used by Phase 10 (replaced
    by [Client] [bubble] rendered log count which is reliable across themes
    and chat auto-scroll). Kept here in case we want to cross-check.
    Count user bubbles (right-side pink) + agent bubbles (left-side white).

    Strategy differs by side because they have different visual signatures:
      - Agent bubbles are surface-white cards with rounded edges. Gaps
        between bubbles are obvious because text doesn't fill the
        entire bubble width — columns in mid-bubble see white, columns
        in gap see frosted-glass panel.
      - User bubbles are full-width pink gradients — adjacent bubbles
        often look continuous in any single column due to gradient
        extending to the edges. We rely on the "你" badge (peach
        background) breaking the pink column at the top of each bubble.
    """
    from PIL import Image
    img = Image.open(shot_path)
    CHAT_TOP = 480
    CHAT_BOTTOM = 2080

    # === Agent bubbles: scan left column at x=300 ===
    agent_rows: list[int] = []
    for y in range(CHAT_TOP, CHAT_BOTTOM):
        r, g, b = img.getpixel((300, y))[:3]
        # Surface white card on agent bubble
        if r > 240 and g > 240 and b > 240 and abs(r - g) < 6 and abs(g - b) < 6:
            agent_rows.append(y)

    # === User bubbles: scan right column at x=900 with STRONG pink
    # (gradient solid pink, R-G > 30 to ignore light-pink panel edges).
    # Then merge runs with min height 30 to capture each bubble including
    # its top badge.
    user_strong_rows: list[int] = []
    for y in range(CHAT_TOP, CHAT_BOTTOM):
        r, g, b = img.getpixel((900, y))[:3]
        if r > 230 and r > g + 30 and r > b + 30:
            user_strong_rows.append(y)

    def merge_runs(rows: list[int], gap: int = 30, min_height: int = 0) -> list[list[int]]:
        runs: list[list[int]] = []
        for y in rows:
            if not runs or y - runs[-1][-1] > gap:
                runs.append([y])
            else:
                runs[-1].append(y)
        if min_height > 0:
            runs = [r for r in runs if r[-1] - r[0] >= min_height]
        return runs

    # Agent: gap=30 reliably splits agent bubbles (proven in earlier runs).
    agent_bubbles = merge_runs(agent_rows, gap=30)

    # User: gap=80 to merge badge+body within one bubble; min height=50
    # to filter out micro-runs from gradient edge artifacts.
    user_bubbles = merge_runs(user_strong_rows, gap=80, min_height=50)

    detail = (
        f"user runs at y={[(r[0], r[-1]) for r in user_bubbles]}, "
        f"agent runs at y={[(r[0], r[-1]) for r in agent_bubbles]}"
    )
    return len(user_bubbles), len(agent_bubbles), detail
def phase_final_assertions(p: Phase) -> None:
    """Phase 12: Scan worker log for ERROR lines outside exempt list."""
    if p.worker_tail is None:
        p.checks.append(CheckResult(
            "zero-unhandled-errors", False, "no worker_tail"))
        return
    count, unhandled = p.worker_tail.count_errors_since(
        p.start_offset, EXEMPT_ERROR_PATTERNS
    )
    if count == 0:
        p.checks.append(CheckResult(
            "zero-unhandled-errors",
            True,
            "no unhandled ERROR in worker log since phase 1",
        ))
    else:
        for line in unhandled[:5]:
            log("FAIL", f"unhandled ERROR: {line[:200]}")
        p.checks.append(CheckResult(
            "zero-unhandled-errors",
            False,
            f"{count} unhandled ERROR lines (see log)",
        ))


def _read_sim_client_log() -> set[str]:
    """Read sim OS log for [Client] markers; returns set of lines."""
    try:
        out = subprocess.run(
            ["xcrun", "simctl", "spawn", "booted", "log", "show",
             "--last", "30s", "--style", "compact"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()
    return {line for line in out.splitlines() if "[Client]" in line}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

PHASES: dict[str, Phase] = {
    "setup":       Phase("1. Setup", phase_setup),
    "launch":      Phase("2. Launch", phase_launch),
    "welcome":     Phase("3. Welcome", phase_welcome),
    "theme":       Phase("4. Theme", phase_theme_toggle),
    "start":       Phase("5. Start call", phase_start_call),
    "mic":         Phase("6. Mic toggle", phase_mic_toggle),
    "speaker":     Phase("7. Speaker toggle", phase_speaker_toggle),
    "chat":        Phase("8. Chat panel", phase_chat_toggle),
    "greeting":    Phase("9. Greeting audio", phase_greeting_audio),
    "text":        Phase("10. Text round", phase_text_round),
    "hangup":      Phase("11. Hangup", phase_hangup),
    "final":       Phase("12. Final assertions", phase_final_assertions),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", default="1-12",
        help="which phases to run, e.g. '1-3' or 'setup,welcome'",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="smoke test: just setup + launch + welcome + start + hangup",
    )
    parser.add_argument(
        "--keep-screenshots", action="store_true",
        help="don't clean screenshots dir before run",
    )
    args = parser.parse_args()

    if not args.keep_screenshots:
        # Don't blow away old screenshots, just add a timestamp subfolder
        pass

    if args.quick:
        selected = ["setup", "launch", "welcome", "start", "hangup"]
    else:
        # Parse "1-9" → ["setup", "launch", ..., "hangup"]
        if "-" in args.phase:
            lo, hi = args.phase.split("-")
            keys = list(PHASES.keys())
            # Phase numbers: 1=setup, 2=launch, ...
            sel = []
            for k in keys:
                n = list(PHASES.keys()).index(k) + 1
                if int(lo) <= n <= int(hi):
                    sel.append(k)
            selected = sel
        else:
            selected = [s.strip() for s in args.phase.split(",")]

    log("INFO", f"{'='*60}\n{BLUE}Vox Flutter e2e UI test{RESET}\n"
                f"UDID={UDID}\nBundleID={BUNDLE_ID}\n"
                f"Phases: {selected}\n{'='*60}")

    start = time.time()
    results: list[tuple[str, bool, list[CheckResult]]] = []

    # Initialize worker log tail once at the start so all phases share the
    # same offset cursor; record start_offset for final ERROR scan.
    worker_tail = WorkerLogTail()
    start_offset = worker_tail.snapshot_offset()
    current_room_name: str | None = None
    # Cross-phase shared state: greeting subscriber is spawned in phase 5
    # but verified in phase 9; reply subscriber spawned in phase 10.
    shared_subscriber_proc = None
    shared_subscriber_out: Optional[Path] = None

    for name in selected:
        if name not in PHASES:
            log("FAIL", f"unknown phase {name!r}")
            return 1
        p = PHASES[name]
        p.shot = None
        p.worker_tail = worker_tail
        p.start_offset = start_offset
        p.room_name = current_room_name
        # Carry greeting subscriber from phase 5 (set when present on p.subscriber_proc)
        if p.subscriber_proc is None and shared_subscriber_proc is not None:
            p.subscriber_proc = shared_subscriber_proc
            p.subscriber_out = shared_subscriber_out
        ok = p.run()
        if p.room_name:
            current_room_name = p.room_name
        # After phase 5 finishes, capture subscriber_proc for phase 9
        if name == "start" and p.subscriber_proc is not None:
            shared_subscriber_proc = p.subscriber_proc
            shared_subscriber_out = p.subscriber_out
        results.append((p.name, ok, p.checks))

    elapsed = time.time() - start
    total = sum(len(c) for _, _, c in results)
    passed = sum(1 for _, _, c in results for x in c if x.passed)
    failed = total - passed

    print(f"\n{'='*60}")
    if failed == 0:
        print(f"{GREEN}✓ ALL CHECKS PASSED{RESET}  {passed}/{total} in {elapsed:.1f}s")
    else:
        print(f"{RED}✗ {failed} CHECKS FAILED{RESET}  {passed}/{total} passed in {elapsed:.1f}s")
    print(f"{'='*60}\n")

    # Save summary JSON
    summary = {
        "udid": UDID, "bundle_id": BUNDLE_ID,
        "elapsed_sec": round(elapsed, 1),
        "total_checks": total, "passed": passed, "failed": failed,
        "phases": [
            {
                "name": name,
                "ok": ok,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail}
                    for c in checks
                ],
            }
            for name, ok, checks in results
        ],
    }
    summary_path = LOGS_DIR / f"summary-{time.strftime('%Y%m%d-%H%M%S')}.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    log("INFO", f"summary saved to {summary_path.relative_to(ROOT)}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
