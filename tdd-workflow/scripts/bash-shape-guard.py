#!/usr/bin/env python3
"""PreToolUse(Bash) guard: two command shapes that cost hours and read as success.

It runs in every session and every subagent, because both shapes kept coming
back after a skill or memory note told the model not to write them: a rule in
text reaches the main session at best, and 70% of the measured cases came from
subagents and workflow agents, which start fresh.

Rule 1, foreground waiting. Refused when the call is NOT `run_in_background`:

  * the command sleeps more than SETTLE_BOUND_S seconds in total
    (`sleep 190; cat <task output>`);
  * a `sleep` inside a loop body (`until grep -q exit log; do sleep 15; done`),
    or `watch`: a loop's total wait cannot be read from its text;
  * `gh run watch` / `gh pr checks --watch`: a foreground watch of an event
    that ends with a notification when it runs in the background.

  The fix is always the same: start the long work (or the wait itself) with
  `run_in_background: true` and act on the completion notification. A short
  settle pause after a signal you just sent (kill, cancel, restart) stays
  allowed in the foreground.

Rule 2, a status that is not the command's. Refused in either mode, unless the
command sets `pipefail`:

  * a pipeline ending in a filter (`tail`, `head`, `sed`, ...) whose status is
    then used: `build | tail -4 && up -d`, `test | tail; echo exit=$?`,
    `if gate | tail; then`. A pipeline reports its LAST command's status, so
    the build's failure becomes the filter's 0. Reading output through a pipe
    is fine; deciding on the pipe's status is what is refused.

A crash in this script allows the command: it guards habits, not security, and
must never be the reason work stops. The release gate is the security guard.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_text import Region, mask_inert, simple_commands  # noqa: E402

# The longest foreground sleep a command may hold, summed over its `sleep`s.
#
# Derived from every foreground Bash call with a `sleep` in this machine's Claude
# Code transcripts, 2026-09-01 to 2026-10-12 (3,589 calls, 105.8 h of wall clock;
# 1,797 of them in subagents). Provenance: the owner's own development sessions,
# which is exactly the population this hook governs.
#   * Calls whose longest sleep is at most 10 s, outside a loop: 855 calls, 3.3 h.
#     They follow a signal the agent just sent and that has no completion event:
#     `kill …; sleep 1`,
#     `gh run cancel …; sleep 5`, a Docker relaunch, `sleep 8` before re-reading
#     a status GitHub recomputes asynchronously.
#   * Single sleeps above 10 s: 2,277 calls, 93.8 h, 89% of all sleep time. The
#     dominant shape is `sleep N; <read a background task's output or a run's
#     status>`, i.e. polling an event that already ends with a notification.
#   * The Bash tool's own default timeout is 120 s; 77 sleeps died on a timeout.
# Re-derive when a refusal is reported on a legitimate settle pause, or when the
# Bash tool gains a native wait.
SETTLE_BOUND_S = 10.0

_SHELL_RUNNER = re.compile(r"(?:^|[\s/])(ssh|bash|sh|zsh|dash|ksh|eval|watch)(?=\s|$)")
_SEPARATORS = ";&|(\n"


def _segment_start(text: str, pos: int) -> int:
    i = pos - 1
    while i >= 0 and text[i] not in _SEPARATORS:
        i -= 1
    return i + 1


def _runs_shell_text(cmd: str, masked_all: str) -> "callable":
    """A quoted string or heredoc body is a COMMAND when the simple command it
    belongs to hands it to a shell: `bash -c '...'`, `ssh host '...'`,
    `ssh host 'bash -s' <<EOF`, `docker exec c sh -c '...'`, `timeout 600 bash -c`."""

    def keep(_cmd: str, region: Region) -> bool:
        if region.kind == "comment":
            return False
        start = _segment_start(masked_all, region.anchor)
        return bool(_SHELL_RUNNER.search(masked_all[start:region.anchor]))

    return keep


def visible_text(cmd: str) -> str:
    masked_all = mask_inert(cmd)
    return mask_inert(cmd, keep=_runs_shell_text(cmd, masked_all))


_CMD_WORD_BEFORE = {"do", "then", "else", "nohup", "exec", "time", "command"}


def _in_command_position(text: str, pos: int) -> bool:
    i = pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i < 0:
        return True
    if text[i] in ";&|({\n'\"`":
        return True
    j = i
    while j >= 0 and (text[j].isalnum() or text[j] == "_"):
        j -= 1
    return text[j + 1:i + 1] in _CMD_WORD_BEFORE


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_UNIT_S = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _sleep_seconds(args: str) -> float | None:
    """Total seconds of `sleep <args>`, or None when an argument is computed at
    runtime (`sleep "$DELAY"`): unknown is not refused, it cannot be derived."""
    total = 0.0
    for tok in args.split():
        if tok.lower() in ("inf", "infinity"):
            return math.inf
        m = _DURATION.match(tok)
        if not m:
            return None
        total += float(m.group(1)) * _UNIT_S[m.group(2)]
    return total


_LOOP_KEYWORD = re.compile(r"(?:^|[;\n&|(])[ \t]*(do|done)(?=[\s;)]|$)")


def _loop_depth_at(text: str, pos: int) -> int:
    depth = 0
    for m in _LOOP_KEYWORD.finditer(text[:pos]):
        depth += 1 if m.group(1) == "do" else -1
    return max(depth, 0)


def foreground_wait(cmd: str) -> str | None:
    """Rule 1. The reason a foreground command waits too long, or None."""
    text = visible_text(cmd)
    total = 0.0
    shown: list[str] = []
    for m in re.finditer(r"\bsleep\b", text):
        if not _in_command_position(text, m.start()):
            continue
        rest = re.match(r"[^;&|)\n'\"`]*", text[m.end():]).group(0)
        if _loop_depth_at(text, m.start()) > 0:
            return f"it sleeps inside a loop (`sleep{rest.rstrip()}` between `do` and `done`)"
        seconds = _sleep_seconds(rest)
        if seconds is not None:
            total += seconds
            shown.append(f"sleep{rest.rstrip()}")
    if total > SETTLE_BOUND_S:
        return (f"it sleeps {('for ever' if math.isinf(total) else f'{total:g} s')} in the "
                f"foreground ({'; '.join(shown)}), above the {SETTLE_BOUND_S:g} s settle pause")
    for m in re.finditer(r"\bwatch\b", text):
        if _in_command_position(text, m.start()):
            return "it runs `watch`, a loop that never ends on its own"
    if re.search(r"\bgh[ \t]+run[ \t]+watch\b", text):
        return "it runs `gh run watch` in the foreground"
    for m in re.finditer(r"\bgh[ \t]+pr[ \t]+checks\b[^;&|\n]*", text):
        if re.search(r"(?:^|\s)--watch\b", m.group(0)):
            return "it runs `gh pr checks --watch` in the foreground"
    return None


# Commands whose exit status says nothing about their input.
_FILTERS = {"tail", "head", "cut", "sed", "awk", "tee", "sort", "uniq", "wc", "tr",
            "column", "cat", "nl", "fold"}
_PIPEFAIL = re.compile(r"\bset\s+-\w*o\s+pipefail\b|\bset\s+-o\s+pipefail\b|\bsetopt\s+pipe_?fail\b", re.I)
_STATUS_IGNORED = re.compile(r"^\s*(true|:|exit\s+0)\s*$")


def _first_word(text: str) -> str:
    for tok in text.split():
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok) or tok in ("command", "\\", "!"):
            continue
        return tok.rsplit("/", 1)[-1]
    return ""


def masked_status(cmd: str) -> str | None:
    """Rule 2. The reason a pipeline's status is read as its producer's, or None."""
    if "|" not in cmd:
        return None
    masked = mask_inert(cmd)
    if _PIPEFAIL.search(masked):
        return None
    cmds = simple_commands(masked)
    k = 0
    while k < len(cmds):
        j = k
        while cmds[j][2] in ("|", "|&") and j + 1 < len(cmds):
            j += 1
        if j > k:  # cmds[k..j] is a pipeline
            producer = masked[cmds[k][0]:cmds[k][1]].strip()
            last = _first_word(masked[cmds[j][0]:cmds[j][1]])
            sep = cmds[j][2]
            words_before = producer.split()[:1]
            consumed = None
            # A producer run as `cmd 2>&1 | filter` is a process whose whole output is
            # being summarised, i.e. one whose outcome is the point; a reader such as
            # `grep … | head && sed …` chains reads whose status nobody depends on.
            captured = cmds[k][2] == "|&" or re.search(r"2>&1\s*$", masked[cmds[k][0]:cmds[k][1]])
            if last in _FILTERS:
                if words_before and words_before[0] in ("if", "elif", "while", "until", "!"):
                    consumed = f"`{words_before[0]}`"
                elif sep == "&&" and captured:
                    consumed = "`&&`"
                elif sep == "||" and captured and j + 1 < len(cmds) and not _STATUS_IGNORED.match(
                        masked[cmds[j + 1][0]:cmds[j + 1][1]]):
                    consumed = "`||`"
                elif sep in (";", "\n") and j + 1 < len(cmds) and "$?" in cmd[cmds[j + 1][0]:cmds[j + 1][1]]:
                    consumed = "`$?`"
            if consumed:
                shown = re.sub(r"\s+", " ", cmd[cmds[k][0]:cmds[j][1]].strip())[:80]
                return (f"the status of `{shown}` is used by {consumed}, but a pipeline's status is "
                        f"its last command's: `{last}` exits 0 when the command before it failed")
        k = j + 1
    return None


def decide(payload: dict) -> str | None:
    tool_input = payload.get("tool_input") or {}
    cmd = str(tool_input.get("command") or "")
    if not cmd or not any(w in cmd for w in ("sleep", "watch", "|")):
        return None
    if not tool_input.get("run_in_background"):
        why = foreground_wait(cmd)
        if why:
            return (f"Refused before running: {why}. A foreground wait holds the session and dies on "
                    f"the Bash timeout, and polling fills the context with the same output. Start the "
                    f"long work, or the wait loop itself, with `run_in_background: true` and act on "
                    f"its completion notification instead of polling its output. A single settle "
                    f"pause of at most {SETTLE_BOUND_S:g} s after a signal you sent (kill, cancel, "
                    f"restart) is allowed. Nothing ran.")
    why = masked_status(cmd)
    if why:
        return (f"Refused before running: {why}, so a failure would read as success. Write the "
                f"output to a file and test the command's own status (`cmd > /tmp/x.log 2>&1; echo "
                f"exit=$?`, then read the file), or put `set -o pipefail` first. Nothing ran.")
    return None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("tool_name") != "Bash":
            return
        reason = decide(payload)
    except Exception:  # a habit guard never blocks work by crashing
        return
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))


if __name__ == "__main__":
    main()
