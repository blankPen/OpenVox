# shellcheck shell=bash

# Shared logging helpers for tooling/scripts/*.
#
# Source from each script with:
#   . "$(dirname "$0")/lib/log.sh"
#
# Functions:
#   info  <msg>...   green checkmark, normal text
#   warn  <msg>...   yellow warning
#   err   <msg>...   red cross, prints to stderr
#   step  <msg>...   bold heading
#   die   <msg>...   err + exit 1
#   have  <cmd>      silent `command -v` check
#   run   <cmd>...   echo + run; abort on failure (uses set -e in caller)
#
# Designed to be cheap: no subshells, no color detection beyond tty.

if [[ -t 1 ]]; then
  _LOG_C_RESET=$'\033[0m'
  _LOG_C_BOLD=$'\033[1m'
  _LOG_C_GRN=$'\033[32m'
  _LOG_C_YEL=$'\033[33m'
  _LOG_C_RED=$'\033[31m'
  _LOG_C_CYN=$'\033[36m'
else
  _LOG_C_RESET=""
  _LOG_C_BOLD=""
  _LOG_C_GRN=""
  _LOG_C_YEL=""
  _LOG_C_RED=""
  _LOG_C_CYN=""
fi

_log() {
  local color="$1"; shift
  printf "%s%s%s\n" "$color" "$*" "$_LOG_C_RESET" >&2
}

step() { _log "${_LOG_C_BOLD}${_LOG_C_CYN}" "==> $*"; }
info() { _log "$_LOG_C_GRN" "  ✓ $*"; }
warn() { _log "$_LOG_C_YEL" "  ! $*"; }
err()  { _log "$_LOG_C_RED" "  ✗ $*"; }
die()  { err "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
