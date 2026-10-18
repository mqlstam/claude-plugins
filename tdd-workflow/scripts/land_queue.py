#!/usr/bin/env python3
"""Does this repository land on `main` through a land queue, and can this checkout use it?

The land queue (Endoxia slice 803: `pnpm ship:land`) is the only way onto `main` in a
repository whose `main` has one. That is a fact about `main`, not about the branch
being shipped: a branch cut before the queue landed has no `ship:land` in its own
`package.json`, and reading the branch's file sent it down the generic flow, to a bare
`gh pr merge` around the queue. So the answer is read from `origin/main`'s
`package.json`, and `ship:land` is the script whose presence there defines the queue.

One definition, two readers:

  * the release skills (/ship, /quickship, /merge) run this file in their context with
    `--fetch`, so a remote-tracking ref nobody refreshed cannot answer for a `main`
    that has moved;
  * the release gate (release-approval-gate.py) imports `on_main()` and reads the local
    ref only, because a PreToolUse hook makes no network call. Every flow that merges
    has fetched `origin main` before it gets there: the skills' context, the generic
    flow's "main moved?" step, and the queue's own integrator.

CLI, run in the repository's checkout:

    python3 land_queue.py [--fetch]

prints one line whose first word is the verdict:

    queue             main lands through the queue, and this checkout has `ship:land`
    merge-main-first  main lands through the queue, and this checkout predates it
    none              main has no land queue: the repository's own flow applies
    unknown: <why>    the fetch failed, or origin/main or this checkout's package.json
                      cannot be read: decide nothing

Exit 0 for the first three, 1 for `unknown`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

LAND_SCRIPT = "ship:land"
MAIN_REF = "refs/remotes/origin/main"

# A local git call: these read one object or one ref. 3 s is what the release gate's
# other git calls allow, two orders of magnitude above the ~10 ms they take, and the
# hook's own budget (30 s, hooks.json) still holds a slow disk.
GIT_LOCAL_TIMEOUT_S = 3

# `git fetch origin main` from a skill's context line: the skill does not load until it
# returns. Measured 2026-10-18 on this machine: 1.44 s for the round trip to GitHub
# (`git ls-remote origin refs/heads/main`, Endoxia). A fetch adds the pack of what
# landed since the last one, a day's commits at most in practice; 60 s is ~40x the
# round trip, room for that pack on a slow link. At the bound the verdict is
# `unknown` and the skill stops with the reason, so a hung network never hangs /ship.
FETCH_TIMEOUT_S = 60


def _git(repo_dir: str, *args: str, timeout: float = GIT_LOCAL_TIMEOUT_S):
    try:
        return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _has_land_script(text: str) -> bool | None:
    try:
        scripts = json.loads(text).get("scripts")
    except (ValueError, AttributeError):
        return None
    if scripts is None:
        return False
    return LAND_SCRIPT in scripts if isinstance(scripts, dict) else None


def on_main(repo_dir: str) -> bool | None:
    """Whether `origin/main`'s package.json, as the local remote-tracking ref holds it,
    has the land script. False when main has no package.json or there is no
    `origin/main` (nothing lands on it through a queue); None when `repo_dir` is no git
    checkout or main's package.json cannot be parsed."""
    if not os.path.isdir(repo_dir):
        return None
    shown = _git(repo_dir, "show", f"{MAIN_REF}:package.json")
    if shown is None:
        return None
    if shown.returncode == 0:
        return _has_land_script(shown.stdout)
    # The ref or the file is missing, or this is no repository at all: only the last is
    # unknown. `rev-parse --git-dir` answers for a worktree as well as a checkout.
    probe = _git(repo_dir, "rev-parse", "--git-dir")
    return False if probe is not None and probe.returncode == 0 else None


def in_checkout(repo_dir: str) -> bool | None:
    """Whether the checkout's own package.json (the working tree's, which is what the
    package manager runs) has the land script. None when it cannot be parsed or the
    directory is no checkout."""
    top = _git(repo_dir, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or not top.stdout.strip():
        return None
    path = os.path.join(top.stdout.strip(), "package.json")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return _has_land_script(text)


def verdict(repo_dir: str, fetch: bool) -> str:
    if fetch:
        fetched = _git(repo_dir, "fetch", "--quiet", "origin", "main", timeout=FETCH_TIMEOUT_S)
        if fetched is None:
            return (f"unknown: `git fetch origin main` did not finish within {FETCH_TIMEOUT_S} s, "
                    f"so origin/main may be stale")
        if fetched.returncode != 0:
            why = (fetched.stderr.strip().splitlines() or [f"exit {fetched.returncode}"])[-1]
            return f"unknown: `git fetch origin main` failed ({why}), so origin/main may be stale"
    main = on_main(repo_dir)
    if main is None:
        return "unknown: origin/main's package.json cannot be read here"
    if not main:
        return "none"
    mine = in_checkout(repo_dir)
    if mine is None:
        return "unknown: this checkout's package.json cannot be read"
    return "queue" if mine else "merge-main-first"


def main(argv: list[str]) -> int:
    unknown_args = [a for a in argv if a != "--fetch"]
    if unknown_args:
        print(f"unknown: usage: land_queue.py [--fetch] (got {' '.join(unknown_args)})")
        return 1
    line = verdict(os.getcwd(), fetch="--fetch" in argv)
    print(line)
    return 1 if line.startswith("unknown") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
