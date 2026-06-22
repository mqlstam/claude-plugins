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
                "  /feature <name>    - New feature with TDD\n"
                "  /refactor <name>   - Refactor with TDD\n"
                "  /ship              - Commit, push, PR, merge (does NOT deploy)\n"
                "  /quickship         - Push directly to main (does NOT deploy)\n"
                "  /merge             - Squash-merge PR into main (does NOT deploy)\n"
                "  /deploy            - Ship main to prod via a deploy-* tag (the ONLY thing that deploys)\n"
                "  /spinup            - Bring up this worktree's private runnable stack\n"
                "  /teardown          - Tear down this worktree's stack (surgical)\n"
                "  /validate          - Check slice completeness"
            )
        }
    }

    print(json.dumps(output))
    sys.exit(0)


if __name__ == '__main__':
    main()
