#!/usr/bin/env python3
"""SessionStart / SubagentStart hook: hand every conversation the Bash command shapes.

The lesson already lived in the main session's memory, and the refusals kept
coming: 70% of the 4,956 worktree-isolation refusals measured 2026-09-01..10-12
came from subagents and workflow agents, which start with a fresh context and
never see that memory. SubagentStart is the one place that reaches each of them
before its first command; SessionStart gives the main conversation the same
text, so there is one source (context/bash-command-shape.md) and no second copy
in a CLAUDE.md.

Context only: these events cannot block. A missing or unreadable file prints
nothing, so a broken plugin checkout never stops a session from starting.
"""
from __future__ import annotations

import json
import os
import sys

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
CONTEXT_FILE = os.path.join(PLUGIN_ROOT, "context", "bash-command-shape.md")

# Claude Code moves a longer `additionalContext` to a file and hands Claude only a
# 2,000-character preview of it (code.claude.com/docs/en/hooks, "Add context for
# Claude"). The whole text must arrive, so it stays under that documented limit.
ADDITIONAL_CONTEXT_LIMIT = 10_000

EVENTS = ("SessionStart", "SubagentStart")


def build(event: str) -> dict | None:
    if event not in EVENTS:
        return None
    try:
        with open(CONTEXT_FILE, encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return None
    if not text or len(text) > ADDITIONAL_CONTEXT_LIMIT:
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    out = build(str(payload.get("hook_event_name") or ""))
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
