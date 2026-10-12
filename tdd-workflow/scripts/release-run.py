#!/usr/bin/env python3
"""Find the Actions run a release started, and (in the background) read its verdict.

    python3 release-run.py find <workflow-file> <sha> [--repo owner/name]
        Foreground. Prints `run_id=<id> url=<url>` once GitHub has registered the
        run for <sha>. Hand that to the user and give the session back.

    python3 release-run.py await <run-id> --deadline-min <n> [--repo owner/name]
        Run with `run_in_background: true`. Exits when the run is completed and
        prints `conclusion=<conclusion> run_id=<id> url=<url>`, read from
        `gh run view --json status,conclusion`, never from a watch's exit code.

Why: a release skill that watched its run in the foreground held the session for
the whole run, 93 minutes of session time a day (devstudy report, 2026-10-07), and
`gh run watch --exit-status` once exited 0 for a run whose conclusion was
`failure` (run 35550388776, 2026-09-21). The verdict is the API's `conclusion`
after `status == completed`; the exit code here only mirrors it.

Exit status: 0 found / success; 1 the run concluded, not `success`;
5 `release_run_find_deadline` or `release_run_await_deadline`; 2 usage or `gh`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# How long GitHub takes to register the run a tag push starts. Measured on
# EndoxiaBV/Endoxia 2026-10-12 from the last 60 tag-push deploy runs: the time from
# the `deploy-YYYYMMDD-HHMMSS` in the tag name to the run's `createdAt` was 3-22 s
# (p50 7 s) for the 45 tags named in UTC. The bound is about 2.7x the largest,
# the same 60 s window the deploy skill used before. Re-derive when a find reaches
# it and the run appears shortly after.
FIND_DEADLINE_S = 60
FIND_POLL_S = 3

# One `gh run view` per poll. A deploy run takes about 22 min (devstudy report),
# so 30 s adds at most ~2% to the time before the verdict arrives, and a 190 min
# wait costs at most 380 reads, far inside GitHub's 5,000 an hour.
AWAIT_POLL_S = 30


def gh_json(args: list[str]) -> object | None:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _repo(repo: str | None) -> list[str]:
    return ["--repo", repo] if repo else []


def find(workflow: str, sha: str, repo: str | None, deadline_s: float, poll_s: float,
         clock=time.monotonic, sleep=time.sleep) -> tuple[int, str]:
    end = clock() + deadline_s
    while True:
        runs = gh_json(["run", "list", "--workflow", workflow, "--commit", sha, "--limit", "20",
                        "--json", "databaseId,url,createdAt,event", *_repo(repo)])
        if isinstance(runs, list) and runs:
            newest = max(runs, key=lambda r: str(r.get("createdAt") or ""))
            return 0, f"run_id={newest.get('databaseId')} url={newest.get('url')}"
        if clock() >= end:
            return 5, (f"release_run_find_deadline workflow={workflow} sha={sha[:12]} "
                       f"after={deadline_s:g}s: no run registered; read `gh run list --workflow {workflow}`")
        sleep(poll_s)


def await_run(run_id: str, repo: str | None, deadline_s: float, poll_s: float,
              clock=time.monotonic, sleep=time.sleep) -> tuple[int, str]:
    end = clock() + deadline_s
    status = "unknown"
    url = "-"
    while True:
        view = gh_json(["run", "view", run_id, "--json", "status,conclusion,url", *_repo(repo)])
        if isinstance(view, dict):
            status = str(view.get("status") or "unknown")
            url = str(view.get("url") or url)
            if status == "completed":
                conclusion = str(view.get("conclusion") or "unknown")
                return (0 if conclusion == "success" else 1), f"conclusion={conclusion} run_id={run_id} url={url}"
        if clock() >= end:
            return 5, (f"release_run_await_deadline run_id={run_id} status={status} url={url} "
                       f"after={deadline_s / 60:g}min: the run is still going; no verdict yet")
        sleep(poll_s)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("workflow")
    f.add_argument("sha")
    f.add_argument("--repo")
    f.add_argument("--deadline-s", type=float, default=FIND_DEADLINE_S)
    f.add_argument("--poll-s", type=float, default=FIND_POLL_S)
    a = sub.add_parser("await")
    a.add_argument("run_id")
    a.add_argument("--repo")
    a.add_argument("--deadline-min", type=float, required=True,
                   help="the workflow's critical path of job timeouts, derived by the caller")
    a.add_argument("--poll-s", type=float, default=AWAIT_POLL_S)
    args = ap.parse_args(argv)
    if args.cmd == "find":
        code, line = find(args.workflow, args.sha, args.repo, args.deadline_s, args.poll_s)
    else:
        code, line = await_run(args.run_id, args.repo, args.deadline_min * 60, args.poll_s)
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
