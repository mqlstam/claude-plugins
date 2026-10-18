#!/usr/bin/env python3
"""
SessionStart hook: Auto-fetch origin and show available commands.
"""
import json
import subprocess
import sys


def git_fetch():
    """Fetch origin silently. Ensures worktrees branch from latest code."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--prune", "--quiet"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass  # Network down is fine, just skip


def git_status():
    """Check if local main is behind origin/main."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        behind = int(result.stdout.strip()) if result.returncode == 0 else 0
        return behind
    except Exception:
        return 0


def main():
    input_data = json.load(sys.stdin)
    session_id = input_data.get('session_id', 'unknown')

    git_fetch()
    behind = git_status()

    sync_note = ""
    if behind > 0:
        sync_note = f"\nNote: local is {behind} commit(s) behind origin/main. Run `git pull` on main if needed.\n"

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                f"Session: {session_id[:12]}\n"
                f"{sync_note}"
                "Commands:\n"
                "  /feature <name>    - New feature, tests+impl in one agent (--layers to fan out)\n"
                "  /refactor <name>   - Refactor; ANALYZE fans out, the build does not (--layers)\n"
                "  /ship              - Commit, gate the change, push, PR, land on main (does NOT deploy)\n"
                "  /quickship         - Same gates, straight to main; /ship where there is a land queue\n"
                "  /merge             - Squash-merge a PR into main, through the land queue if any\n"
                "  /deploy            - Release main: join the deploy run building it, else tag (Endoxia: STAGING)\n"
                "  /promote           - Release what staging serves to production, after the full suite (Endoxia)\n"
                "  /spinup            - Bring up this worktree's private runnable stack\n"
                "  /teardown          - Tear down this worktree's stack (surgical)\n"
                "  /validate          - Check slice completeness\n"
                "\n"
                "Gate (repos with pnpm ship:gate): proportional to the change, once per tree.\n"
                "VERIFY runs it; /ship, pre-push and the land queue reuse its record for the\n"
                "identical tree judged against the same origin/main. A gate with heavy work\n"
                "waits for the machine's slot (one by default: such gates queue FIFO); one\n"
                "with nothing heavy takes no slot. The land queue (pnpm ship:land) tests the\n"
                "merged result once and lands in order, so a branch never merges main to be\n"
                "re-tested. Never sleep-poll a long command -- run_in_background and wait for\n"
                "the completion notification."
            )
        }
    }

    print(json.dumps(output))
    sys.exit(0)


if __name__ == '__main__':
    main()
