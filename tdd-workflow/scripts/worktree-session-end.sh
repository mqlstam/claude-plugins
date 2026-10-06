#!/usr/bin/env bash
# SessionEnd dispatcher for per-worktree runnable stacks.
#
# Lives in the PLUGIN, not in the repository, for one measured reason
# (Claude Code v2.1.289, probed 2026-10-05 by driving real `claude --worktree`
# sessions through a pseudo-terminal and exiting them the way a person does):
#
#   at SessionEnd   cwd                 = the MAIN checkout (Claude Code moves the
#                                         session back when it leaves a worktree)
#                   CLAUDE_PROJECT_DIR  = the worktree path
#                   the worktree        = already DELETED when you chose "Remove"
#                                         (or the session was clean), still there
#                                         when you chose "Keep"
#
# A hook command that starts `$CLAUDE_PROJECT_DIR/scripts/...` therefore finds no
# script after "Remove", and a script that trusts the payload's `cwd` concludes
# "this is the main checkout, do nothing" after "Keep". Both happened, silently,
# on every exit: neither cleanup path had run since the session started moving
# back to the main checkout on exit.
#
# So this script keys on CLAUDE_PROJECT_DIR, which still names the worktree:
#   * the worktree still exists ("Keep")  -> the repo's own reversible soft-stop
#     (scripts/worktree-soft-stop.sh), handed the payload with `cwd` corrected to
#     the worktree, so an older copy of that script works unchanged;
#   * the worktree is gone ("Remove")     -> the main checkout's
#     scripts/worktree-retire.sh --dir <worktree>, detached into its own session
#     so it outlives this hook and the exiting `claude`.
# A repository without those scripts is not a worktree-runtime repository, and
# nothing happens.
#
# ENDOXIA_WORKTREE_RETIRE_SCRIPT overrides the retire script's path. It exists
# for the end-to-end probe, which must run a retire script that is not on the
# main checkout yet; nothing else sets it.
set -uo pipefail

input="$(cat 2>/dev/null || true)"
WT="${CLAUDE_PROJECT_DIR:-}"
WT="${WT%/}"
case "$WT" in
  */.claude/worktrees/?*) ;;
  *) exit 0 ;;
esac
name="${WT##*/}"
case "$WT" in */.claude/worktrees/"$name") ;; *) exit 0 ;; esac

reason="$(printf '%s' "$input" | sed -n 's/.*"reason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
# /clear and resume are not the session closing; the stack is still wanted.
case "$reason" in clear|resume) exit 0 ;; esac

MAIN="${WT%%/.claude/worktrees/*}"
LOG="$HOME/.claude/endoxia-worktree/reap.log"
note() {
  mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
  printf '%s session-end: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG" 2>/dev/null || true
}

# Run a command in a new session, detached from this hook's process group and
# terminal, so neither the hook's end nor the exiting `claude` can take it down.
# macOS ships no setsid(1); the double fork + os.setsid() is the same mechanism
# as the repo's scripts/worktree-dev-detached.sh.
detach() {
  python3 - "$@" <<'PY'
import os
import sys

if os.fork():
    os._exit(0)
os.setsid()
if os.fork():
    os._exit(0)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
os.execvp(sys.argv[1], sys.argv[1:])
PY
}

if [ -d "$WT" ]; then
  stop="$WT/scripts/worktree-soft-stop.sh"
  [ -f "$stop" ] || exit 0
  fixed="$(printf '%s' "$input" | WT="$WT" python3 -c '
import json, os, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except ValueError:
    data = {}
data["cwd"] = os.environ["WT"]
print(json.dumps(data))
' 2>/dev/null)"
  [ -n "$fixed" ] || fixed="{\"cwd\":\"$WT\",\"reason\":\"$reason\"}"
  note "kept $WT — soft-stop"
  printf '%s' "$fixed" | bash "$stop" >/dev/null 2>&1 || true
  exit 0
fi

retire="${ENDOXIA_WORKTREE_RETIRE_SCRIPT:-$MAIN/scripts/worktree-retire.sh}"
if [ -f "$retire" ]; then
  note "removed $WT — retiring its stack, processes and browser"
  detach bash "$retire" --dir "$WT"
elif [ -f "$MAIN/scripts/worktree-reap-orphans.sh" ]; then
  # A main checkout older than worktree-retire.sh still has the reaper, which
  # tears down every stack whose worktree is gone.
  note "removed $WT — main checkout has no worktree-retire.sh yet; running the reaper"
  detach bash "$MAIN/scripts/worktree-reap-orphans.sh"
fi
exit 0
