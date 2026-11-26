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
                "  /feature <name>    - New feature, built by one agent (--layers to fan out)\n"
                "  /refactor <name>   - Refactor; ANALYZE fans out, the build does not (--layers)\n"
                "  /ship              - Land the reviewed PR head (squash-merge or queue); ends there, no deploy\n"
                "  /quickship         - Same local checks, push directly to main\n"
                "  /merge             - Squash-merge a PR directly into main\n"
                "  /deploy            - Main to STAGING sooner: join the deploy run building it, else dispatch (no tag)\n"
                "  /promote           - Release what staging serves to production, after the full suite (Endoxia)\n"
                "  /spinup            - Bring up this worktree's private runnable stack\n"
                "  /teardown          - Tear down this worktree's stack (surgical)\n"
                "  /validate          - Check a slice: real failures caught, layers connected\n"
                "\n"
                "Local validation runs this checkout's precheck, else its ship:check, else its own\n"
                "lint, typecheck, test, build and assertions, before the PR opens; the merge\n"
                "queue's required check is the proof. VERIFY is checked before committing; main\n"
                "is merged in only on a conflict, and that alone does not restart VERIFY.\n"
                "Worktrees run independently; /ship squash-merges the exact PR head directly.\n"
                "Main CI judges the merged range; red main is fixed forward, never blocks ships.\n"
                "Staging rolls main hourly when it moved and before every promote, watched by one\n"
                "session; it refuses known red health. Promote gates the\n"
                "exact production SHA with the full suite or a verified green for that SHA.\n"
                "Never sleep-poll a long command: background it and wait for notification."
            )
        }
    }

    print(json.dumps(output))
    sys.exit(0)


if __name__ == '__main__':
    main()
