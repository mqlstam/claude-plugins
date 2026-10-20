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
                "  /ship              - Commit, merge main, check, push, PR, squash-merge (does NOT deploy)\n"
                "  /quickship         - Same local checks, push directly to main\n"
                "  /merge             - Squash-merge a PR directly into main\n"
                "  /deploy            - Release main: join the deploy run building it, else tag (Endoxia: STAGING)\n"
                "  /promote           - Release what staging serves to production, after the full suite (Endoxia)\n"
                "  /spinup            - Bring up this worktree's private runnable stack\n"
                "  /teardown          - Tear down this worktree's stack (surgical)\n"
                "  /validate          - Check slice completeness\n"
                "\n"
                "Local validation uses this checkout's ship:check when present; otherwise its own\n"
                "lint, typecheck, test, build and assertions. VERIFY is checked before committing\n"
                "and merging origin/main in; integration alone does not restart VERIFY.\n"
                "Worktrees run independently; /ship squash-merges the exact PR head directly.\n"
                "Main CI judges the merged range; red main is fixed forward, never blocks ships.\n"
                "Staging refuses known red health and warns when unknown; promote gates the\n"
                "exact production SHA with the full suite or a verified green for that SHA.\n"
                "Never sleep-poll a long command: background it and wait for notification."
            )
        }
    }

    print(json.dumps(output))
    sys.exit(0)


if __name__ == '__main__':
    main()
