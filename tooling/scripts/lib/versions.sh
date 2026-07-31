# shellcheck shell=bash

# Version helpers — extract app versions from each app's manifest.
#
# . "$(dirname "$0")/lib/versions.sh"
#
# Functions:
#   app_version <name>            echo version string for an app
#   release_version_from_tag      strip leading "v" from $RELEASE_TAG
#   release_tag                   echo current tag (set RELEASE_TAG=... to override)

# Print the version of an app from its manifest. Args: agentd | openvox | voice-client
# Returns non-zero if the app / manifest is not present (skips silently).
app_version() {
  local app="$1" repo_root="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
  case "$app" in
    agentd)
      local pkg="$repo_root/apps/agentd/package.json"
      [[ -f "$pkg" ]] || return 1
      node -e "console.log(require('$pkg').version)" 2>/dev/null || return 1
      ;;
    openvox)
      local pyproject="$repo_root/apps/voice-agent/pyproject.toml"
      [[ -f "$pyproject" ]] || return 1
      grep -E '^version\s*=' "$pyproject" | head -1 | sed -E 's/.*"([^"]+)".*/\1/'
      ;;
    voice-client)
      local pubspec="$repo_root/apps/voice-client/pubspec.yaml"
      [[ -f "$pubspec" ]] || return 1
      grep -E '^version:' "$pubspec" | head -1 | awk '{print $2}' | sed 's/+.*//'
      ;;
    *)
      return 1
      ;;
  esac
}

# Echo the release version (no leading 'v'). Override by exporting RELEASE_TAG.
release_version_from_tag() {
  local tag="${RELEASE_TAG:-${GITHUB_REF_NAME:-}}"
  [[ "$tag" =~ ^v(.+)$ ]] && echo "${BASH_REMATCH[1]}" || echo "${tag}"
}

release_tag() {
  local tag="${RELEASE_TAG:-${GITHUB_REF_NAME:-dev}}"
  echo "$tag"
}
