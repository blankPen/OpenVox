# install.ps1 — Bootstrap an openvox development environment on Windows.
#
# What it does (each step is skipped via -No* switch):
#   1. Verify platform is Windows (macOS/Linux users: scripts/install.sh).
#   2. Detect Python 3.10+ on PATH (override with -Python <path>).
#   3. Create .venv at repo root (idempotent: reuse if present).
#   4. Install openvox_worker + livekit-plugins-volcengine editable with --no-deps.
#   5. flutter pub get for apps/voice-client (skipped if flutter missing or -NoFlutter).
#   6. pnpm/npm install + build for apps/agentd (skipped if node missing or -NoNode).
#   7. docker compose up -d for infra/ (skipped if docker missing or -NoLiveKit).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#   .\scripts\install.ps1 -NoFlutter
#   .\scripts\install.ps1 -NoNode
#   .\scripts\install.ps1 -NoLiveKit
#   .\scripts\install.ps1 -Python C:\Python311\python.exe
#
# Notes:
#   - Idempotent: re-running picks up new code without wiping state.
#   - Does NOT modify ~/.openvox or ~/.agentd (those are written by `openvox init`).
#   - iOS / Android client builds require macOS — this script only bootstraps the
#     Python agent + Node agentd + LiveKit + (optionally) Flutter pub get.
#     `flutter run` for desktop / web targets works on Windows; mobile targets do not.

[CmdletBinding()]
param(
  [switch]$NoLiveKit,
  [switch]$NoFlutter,
  [switch]$NoNode,
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path "$ScriptDir\..").Path

# Colors (Windows 10 1607+ / PowerShell 5.1+ supports ANSI; fall back gracefully)
function Write-Step { param($m) Write-Host "[install] $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "[ ok  ] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[warn ] $m" -ForegroundColor Yellow }
function Write-Err  { param($m) Write-Host "[err  ] $m" -ForegroundColor Red }
function Die       { param($m) Write-Err $m; exit 1 }

# Sanity: are we at the repo root?
if (-not (Test-Path "$RepoRoot\apps\voice-agent")) -or (-not (Test-Path "$RepoRoot\tooling")) {
  Die "scripts\install.ps1 must be run from inside the openvox repo (missing apps\voice-agent or tooling\)"
}

# ---- Step 1: platform ----
Write-Step "Step 1/5 · Detecting platform"
if ($env:OS -ne "Windows_NT") {
  Die "this script targets Windows. On macOS/Linux use scripts/install.sh"
}
Write-Ok "platform=Windows"

# ---- Step 2: python ----
Write-Step "Step 2/5 · Detecting Python"
$PyCmd = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $PyCmd) {
  Die "$Python not found. Install Python 3.10+ (winget install Python.Python.3.11) or pass -Python C:\path\to\python.exe"
}
$PythonExe = $PyCmd.Source
$PyVer = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$Major, $Minor = $PyVer.Split('.')
if ([int]$Major -lt 3 -or ([int]$Major -eq 3 -and [int]$Minor -lt 10)) {
  Die "python $PyVer found, but openvox needs 3.10+ (use -Python <path> or install 3.10+)"
}
Write-Ok "python=$PythonExe ($PyVer)"

# ---- Step 3: venv ----
Write-Step "Step 3/5 · Creating .venv (idempotent)"
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvDir)) {
  & $PythonExe -m venv $VenvDir
  if ($LASTEXITCODE -ne 0) { Die "venv creation failed" }
  Write-Ok "created $VenvDir"
} else {
  Write-Ok "$VenvDir already exists (reusing)"
}
& $VenvPython -m pip install --quiet --upgrade pip wheel setuptools
Write-Ok "pip upgraded"

# ---- Step 4: openvox_worker + volcengine plugin ----
Write-Step "Step 4/5 · Installing openvox_worker + Volcengine plugin (editable, --no-deps)"
Push-Location "$RepoRoot\apps\voice-agent"
& $VenvPython -m pip install --quiet -e . --no-deps
if ($LASTEXITCODE -ne 0) { Pop-Location; Die "pip install openvox_worker failed" }
& $VenvPython -m pip install --quiet -e ./plugins/livekit-plugins-volcengine --no-deps
if ($LASTEXITCODE -ne 0) { Pop-Location; Die "pip install livekit-plugins-volcengine failed" }
Pop-Location
Write-Ok "openvox installed (editable) at $VenvDir"

# ---- Step 5: extras ----
Write-Step "Step 5/5 · Extras (flutter, agentd, LiveKit)"

if (-not $NoFlutter) {
  if (Test-Path "$RepoRoot\apps\voice-client") {
    $flutterCmd = Get-Command flutter -ErrorAction SilentlyContinue
    if ($flutterCmd) {
      Push-Location "$RepoRoot\apps\voice-client"
      if (-not (Test-Path .env) -and (Test-Path .env.example)) {
        Copy-Item .env.example .env
        Write-Warn "created .env from .env.example — edit it before flutter run"
      }
      & flutter --no-version-check pub get
      Pop-Location
      Write-Ok "flutter deps installed"
    } else {
      Write-Warn "flutter not found in PATH — skipping voice-client deps"
    }
  }
} else {
  Write-Step "  · flutter skipped (-NoFlutter)"
}

if (-not $NoNode) {
  if (Test-Path "$RepoRoot\apps\agentd") {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCmd) {
      Push-Location "$RepoRoot\apps\agentd"
      $pnpmCmd = Get-Command pnpm -ErrorAction SilentlyContinue
      if ($pnpmCmd) {
        $env:CI = "true"
        & pnpm install --frozen-lockfile
        & pnpm build
      } else {
        Write-Warn "pnpm not found, falling back to npm"
        & npm ci
        if ($LASTEXITCODE -ne 0) { & npm install }
        & npm run build
      }
      Pop-Location
      Write-Ok "agentd built"
    } else {
      Write-Warn "node not found in PATH — skipping agentd build"
    }
  }
} else {
  Write-Step "  · agentd skipped (-NoNode)"
}

if (-not $NoLiveKit) {
  if (Test-Path "$RepoRoot\infra\docker-compose.yml") {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCmd) {
      Push-Location "$RepoRoot\infra"
      & docker compose up -d
      Pop-Location
      Write-Ok "LiveKit container(s) up (run 'docker compose ps' to verify)"
    } else {
      Write-Warn "docker not found — skipping LiveKit. Install Docker Desktop, then 'cd infra; docker compose up -d'"
    }
  }
} else {
  Write-Step "  · LiveKit skipped (-NoLiveKit)"
}

Write-Ok "install complete"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  openvox init                    # write ~/.openvox/config.json (choose LLM backend)"
Write-Host "  openvox start --yes             # start the selected backend + LiveKit worker"
Write-Host "  cd apps\voice-client; flutter run   # optional: start the Flutter UI (desktop / web only on Windows)"
Write-Host ""
Write-Host "NOTE: iOS / Android builds need macOS + Xcode / Android SDK. From Windows you can develop"
Write-Host "the Python agent and run pytest, but mobile client builds require a macOS host."
Write-Host ""
Write-Host "See USAGE.md for the full command matrix and troubleshooting."
