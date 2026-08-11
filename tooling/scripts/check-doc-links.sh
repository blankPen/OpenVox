#!/usr/bin/env bash
# check-doc-links.sh — verify internal (./...) links in OpenVox markdown docs.
#
# Scans all *.md files under specified roots (default: top-level + tooling/ + infra/ +
# apps/voice-client/), extracts links of the form [text](./path#anchor), and verifies:
#   - target file/dir exists (relative to source file)
#   - if anchor present, the anchor's H2/H3 heading exists in target file
#
# Exits non-zero on any broken link; prints one line per broken link with file:line.
#
# Usage:
#   ./tooling/scripts/check-doc-links.sh              # default roots
#   ./tooling/scripts/check-doc-links.sh --strict     # also warn on missing files (404 vs anchor mismatch)
#   ./tooling/scripts/check-doc-links.sh --quiet      # only print summary
#   ./tooling/scripts/check-doc-links.sh docs/ openwiki/  # custom roots
#
# Notes:
#   - Skips openwiki/ by default (it's CI-generated; broken links there are expected).
#   - Skips external URLs (http:// https://).
#   - Skips pure anchor links within the same file (#section).
#   - Skips gitignored files (uses git ls-files under each root when in a git repo).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/log.sh
. "$SCRIPT_DIR/lib/log.sh"

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_ROOTS=(
  "README.md" "INSTALLATION.md" "USAGE.md" "CONTRIBUTING.md"
  "ARCHITECTURE.md" "CHANGELOG.md" "CLAUDE.md" "AGENTS.md"
  "tooling/README.md" "infra/README.md" "apps/voice-client/README.md"
  "apps/voice-agent/README.md" "apps/voice-agent/CLAUDE.md" "apps/agentd/README.md"
  "shared/README.md"
)
STRICT=0
QUIET=0
ROOTS=()

usage() {
  cat <<'EOF'
Usage: ./tooling/scripts/check-doc-links.sh [options] [root ...]

Verify internal links in OpenVox markdown docs.

Options:
  --strict       also report missing target files (default: only anchors / same-file errors)
  --quiet        only print summary, not per-link errors
  -h, --help     show this help

With no arguments, scans the default roots (top-level *.md + per-area README/CLAUDE.md).
You can also pass specific root files/dirs to scan.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict) STRICT=1; shift ;;
    --quiet)  QUIET=1;  shift ;;
    -h|--help) usage; exit 0 ;;
    *)        ROOTS+=("$1"); shift ;;
  esac
done

if [[ ${#ROOTS[@]} -eq 0 ]]; then
  ROOTS=("${DEFAULT_ROOTS[@]}")
fi

# ── GitHub-style slug helpers ──────────────────────────────────────────────
# GitHub anchor slug algorithm (per GFM):
#   1. Convert to lowercase
#   2. Remove characters that are not letters, digits, hyphens, underscores, or CJK
#   3. Replace whitespace with hyphens
# We also strip leading numbering like "8. " or "1、" or "3) " that GitHub ignores.
# Uses perl for Unicode-safe regex (BSD sed on macOS lacks CJK character classes).

slugify_anchor() {
  # Input: anchor string (may include URL-encoded chars)
  perl -CSDA -pe '
    s/%20/ /g;
    $_ = lc;
    s/\s+/-/g;
    s/[^\w\x{4e00}-\x{9fff}\x{3040}-\x{309f}\x{30a0}-\x{30ff}-]//g;
  ' <<< "$1"
}

slugify_heading() {
  # Input: raw heading line (e.g. "## 8. 发布到 GitHub Release")
  perl -CSDA -pe '
    s/^#{1,6}\s+//;
    s/^\d+([.\x{3001}\)])?\s+//;
    $_ = lc;
    s/\s+/-/g;
    s/[^\w\x{4e00}-\x{9fff}\x{3040}-\x{309f}\x{30a0}-\x{30ff}-]//g;
  ' <<< "$1"
}

# ── Collect markdown files to scan ─────────────────────────────────────────
collect_files() {
  local root="$1"
  if [[ -f "$root" ]]; then
    echo "$root"
  elif [[ -d "$root" ]]; then
    # Use git ls-files if in a git repo (skip gitignored); fall back to find.
    if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      git -C "$REPO_ROOT" ls-files "$root" -- '*.md' 2>/dev/null
    else
      find "$root" -type f -name '*.md' -not -path '*/node_modules/*' -not -path '*/.venv/*'
    fi
  fi
}

# Collect into newline-separated string, then iterate via here-string.
# (Using mapfile requires bash 4+; macOS default bash is 3.2.)
MD_FILES_STR="$(
  for r in "${ROOTS[@]}"; do
    cd "$REPO_ROOT"
    collect_files "$r"
  done | sort -u
)"

if [[ -z "$MD_FILES_STR" ]]; then
  die "no markdown files found under roots: ${ROOTS[*]}"
fi

md_file_count=$(echo "$MD_FILES_STR" | wc -l | tr -d ' ')
step "Scanning ${md_file_count} markdown files"

# ── Extract and validate links ─────────────────────────────────────────────
# Link regex (from CommonMark spec, simplified):
#   \[text\]\(\./path(#anchor)?\)
LINK_RE='\]\(\.\/([^)#]+)(#[^)]+)?\)'

broken=0
checked=0

while IFS= read -r md; do
  [[ -z "$md" ]] && continue
  while IFS= read -r match; do
    [[ -z "$match" ]] && continue
    # Extract relative target and optional anchor.
    if [[ "$match" =~ ^([^#]+)(#(.+))?$ ]]; then
      target="${BASH_REMATCH[1]}"
      anchor="${BASH_REMATCH[3]:-}"
    else
      continue
    fi

    # Strip trailing slash (directory references).
    target="${target%/}"
    # Source file's directory.
    src_dir="$(dirname "$md")"
    # Resolved target relative to repo root.
    if [[ "$target" == /* ]]; then
      resolved="${REPO_ROOT}${target}"
    else
      resolved="$(cd "$src_dir" 2>/dev/null && cd "$REPO_ROOT" 2>/dev/null; realpath -m "$src_dir/$target" 2>/dev/null || echo "$src_dir/$target")"
    fi

    # Strip leading ./ for display.
    display_target="./$target"
    [[ -n "$anchor" ]] && display_target="${display_target}#${anchor}"

    checked=$((checked + 1))

    # Check target exists (file or directory).
    if [[ ! -e "$resolved" ]]; then
      if [[ "$STRICT" -eq 0 ]]; then
        # Non-strict: skip missing files silently (they may be intentional placeholders).
        continue
      fi
      if [[ "$QUIET" -eq 0 ]]; then
        err "${md}: broken link → ${display_target} (target not found)"
      fi
      broken=$((broken + 1))
      continue
    fi

    # If anchor present, verify it exists in target as H2 or H3 heading.
    if [[ -n "$anchor" && -f "$resolved" ]]; then
      # Strip URL-encoded chars (e.g. %20) and normalize to GitHub-style slug.
      anchor_norm="$(slugify_anchor "$anchor")"

      # Check if any H2/H3 heading in the target file slugs to anchor_norm.
      heading_match=0
      while IFS= read -r heading_line; do
        [[ -z "$heading_line" ]] && continue
        h_slug="$(slugify_heading "$heading_line")"
        if [[ "$h_slug" == "$anchor_norm" ]]; then
          heading_match=1
          break
        fi
      done < <(grep -E "^#{2,3}[[:space:]]" "$resolved" 2>/dev/null || true)

      if [[ "$heading_match" -eq 0 ]]; then
        if [[ "$QUIET" -eq 0 ]]; then
          err "${md}: broken anchor → ${display_target} (heading not found)"
        fi
        broken=$((broken + 1))
      fi
    fi
  done < <(grep -oE '\]\(\.\/[^)]+\)' "$md" 2>/dev/null | sed -E 's/^\]\(\.\///; s/\)$//' || true)
done <<< "$MD_FILES_STR"

step "Done"
info "checked ${checked} links across ${md_file_count} files"
if [[ "$broken" -gt 0 ]]; then
  err "${broken} broken link(s) found"
  exit 1
fi
info "all links OK"
