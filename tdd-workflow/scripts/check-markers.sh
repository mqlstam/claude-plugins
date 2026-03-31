#!/usr/bin/env bash
# PostToolUse hook: Enforce marker conventions (CLAUDE.md §3).
# Blocks (exit 2) and forces Claude to fix violations.

set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Skip non-code files
if [[ -z "$FILE_PATH" ]] || [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

# Skip config, docs, and non-source files
case "$FILE_PATH" in
  *.md|*.json|*.yaml|*.yml|*.toml|*.env*|*.lock|*.css|*.html) exit 0 ;;
esac

# Check for bare markers without colon+reason suffix
BARE_MARKERS=$(grep -nE '\b(TODO|FIXME|HACK|XXX)\b' "$FILE_PATH" | grep -vE '(TODO:|FIXME:|HACK:|XXX:|STUB:|HARDCODED:|REVIEW:)' || true)

if [[ -n "$BARE_MARKERS" ]]; then
  echo "Bare marker violation in $FILE_PATH:" >&2
  echo "$BARE_MARKERS" >&2
  echo "" >&2
  echo "Fix: use STUB:reason, TODO:ticket, HARDCODED:reason, or REVIEW:reason" >&2
  exit 2
fi

exit 0
