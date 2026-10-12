#!/usr/bin/env python3
"""Wait until GitHub has settled a PR's mergeability for the commit just pushed.

    python3 wait-mergeable.py <pr> --head <sha> [--repo owner/name]

Run it with `run_in_background: true` and act on the completion notification.

Why: GitHub recomputes mergeability in the background after every push. Until it
has, `mergeStateStatus` reads UNKNOWN, or still describes the PREVIOUS head, and a
`gh pr merge` in that window answers "merge conflicts" for a branch that has
none. That race made push-to-merge p90 26.6 min (devstudy report, 2026-10-07):
the merge failed, the session merged main again, re-ran the whole gate, and
raced again. A state counts only once `headRefOid` is the commit we pushed.

Exit status and the last line printed (`merge_state=<STATE> head=<sha>`):
  0  CLEAN or HAS_HOOKS: merge now, in the same minute.
  3  DIRTY or BEHIND: main moved or conflicts. Fetch and check
     `git rev-list --count HEAD..origin/main` before merging main in.
  4  BLOCKED: a required check or review is missing. Report it; do not force.
  5  merge_state_wait_deadline: GitHub had not settled the state by the bound.
  6  UNSTABLE at the bound: mergeable, but a check that is not required is
     failing or still pending. Name it (`gh pr checks <pr>`) when you merge.
  7  the PR is no longer open (merged or closed): read `gh pr view`.
  2  usage, or `gh` could not be run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# The longest this waits for GitHub to settle. Measured in this machine's Claude
# Code transcripts, 2026-09-01 to 2026-10-12 (the owner's own ships): of 7 times a
# PR was read as UNKNOWN and read again later, 6 had settled within 26 s (p50 14 s);
# the 7th was only read again after 447 s, an upper bound rather than a settle
# time. 180 s is about 7x the longest observed settle, so a slow recompute still
# passes, and a state that does not settle is reported within three minutes
# instead of turning into another round of the race. Re-derive when a wait here
# reaches the bound and the state settles shortly after.
DEADLINE_S = 180

# One `gh pr view` per poll: 36 reads at most, far inside GitHub's 5,000 an hour,
# and a settled state is noticed within 5 s, under the 14 s median settle.
POLL_S = 5

SETTLED = {"CLEAN": 0, "HAS_HOOKS": 0, "DIRTY": 3, "BEHIND": 3, "BLOCKED": 4}


def read(pr: str, repo: str | None) -> dict | None:
    cmd = ["gh", "pr", "view", pr, "--json", "state,mergeStateStatus,headRefOid"]
    if repo:
        cmd += ["--repo", repo]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def wait(pr: str, head: str, repo: str | None, deadline_s: float, poll_s: float,
         clock=time.monotonic, sleep=time.sleep) -> tuple[int, str]:
    end = clock() + deadline_s
    last = "UNKNOWN"
    last_head = "-"
    while True:
        view = read(pr, repo)
        if view is not None:
            last_head = str(view.get("headRefOid") or "-")
            if view.get("state") != "OPEN":
                return 7, f"pr_state={view.get('state')} head={last_head}"
            if last_head.startswith(head) or head.startswith(last_head):
                last = str(view.get("mergeStateStatus") or "UNKNOWN")
                if last in SETTLED:
                    return SETTLED[last], f"merge_state={last} head={last_head}"
        if clock() >= end:
            if last == "UNSTABLE":
                return 6, f"merge_state=UNSTABLE head={last_head}"
            return 5, (f"merge_state_wait_deadline merge_state={last} head={last_head} "
                       f"after={deadline_s:g}s (expected head {head})")
        sleep(poll_s)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pr")
    ap.add_argument("--head", required=True, help="the commit just pushed")
    ap.add_argument("--repo")
    ap.add_argument("--deadline-s", type=float, default=DEADLINE_S)
    ap.add_argument("--poll-s", type=float, default=POLL_S)
    args = ap.parse_args(argv)
    code, line = wait(args.pr, args.head, args.repo, args.deadline_s, args.poll_s)
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
