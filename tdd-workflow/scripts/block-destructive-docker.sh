#!/usr/bin/env bash
# PreToolUse(Bash) guard: block the agent from DIRECTLY running volume-wiping
# docker commands (`docker compose ... down -v|--volumes`, `docker volume
# rm|prune`). The audited per-worktree teardown script runs its `docker volume
# rm` as a SUBPROCESS the hook never sees (the hook inspects the tool-call
# command string, not the script's children) — so surgical teardown still works.
# Fires even under --dangerously-skip-permissions: PreToolUse hooks run before,
# and take precedence over, the permission system.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
[ -z "$cmd" ] && cmd="$input"   # fallback if jq is unavailable: scan the raw input

if printf '%s' "$cmd" | grep -qE 'docker[[:space:]]+compose[^|;&]*down[^|;&]*(-v|--volumes)|docker[[:space:]]+volume[[:space:]]+(rm|prune)'; then
  jq -n --arg c "$cmd" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: ("Volume-wiping docker command blocked. For per-worktree teardown use ./scripts/teardown-worktree.sh (surgical, wt-* only). Blocked: " + $c)
    }
  }' 2>/dev/null || { echo "Volume-wiping docker command blocked by policy." >&2; exit 2; }
  exit 0
fi
exit 0
