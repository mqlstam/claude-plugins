#!/usr/bin/env python3
"""Find the Actions run a release started, wait for its verdict, and say why it failed.

    python3 release-run.py find <workflow-file> <sha> [--repo owner/name]
        Prints `run_id=<id> url=<url>` once GitHub has registered the run for <sha>.

    python3 release-run.py await <run-id> --deadline-min <n> [--slice-min <m>] [--repo owner/name]
        Exits when the run is completed and prints `conclusion=<conclusion> run_id=<id>
        url=<url>`, read from `gh run view --json status,conclusion`, never from a
        watch's exit code. A conclusion other than `success` is followed by the reason
        (see `reason`). `--deadline-min` counts from the run's own start, so a session
        that waits in slices of `--slice-min` re-runs the same command and the bound
        stays the run's, not the slice's.

    python3 release-run.py reason <run-id> [--repo owner/name]
        Why a completed run did not succeed: each failed job, its failed steps and its
        check-run annotations. A run GitHub refused to start for billing is named in
        one line as the owner's action.

Why the session waits in the foreground (owner, 2026-10-20). Until then the release
skills started `await` in the background and ended their turn, to save the ~93 minutes
of session time a day a foreground watch had held (devstudy report, 2026-10-07). On
2026-10-20 a staging release (run 39493655874) failed 7 s after its dispatch because
GitHub Actions refused every job for billing, and nobody noticed: the T3 thread that
started it had settled 2 s before. It was the third billing refusal in 8 days. A
release is now watched to its end inside the turn, in slices that fit one foreground
Bash call (`FOREGROUND_SLICE_MIN`).

Why the verdict is the API's: `gh run watch --exit-status` once exited 0 for a run
whose conclusion was `failure` (run 35550388776, 2026-09-21). The verdict is the
API's `conclusion` after `status == completed`; the exit code here only mirrors it.

Exit status: 0 found / success; 1 the run concluded, not `success`;
5 `release_run_find_deadline`, or `release_run_await_deadline NOT CONCLUDED` (the run is still open: never report it as done);
6 `release_run_still_running` (the slice ended, the deadline has not: run it again);
2 usage or `gh`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
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

# One slice of a foreground wait. Claude Code's Bash tool stops a foreground command
# at its `timeout`, at most 600000 ms (10 min); a slice of 9 leaves a minute for the
# last poll (`GH_CALL_TIMEOUT_S`) and the reason reads. Should the tool still stop the
# command, nothing is lost: the verdict lives in the API, and the next slice reads
# a completed run on its first poll.
FOREGROUND_SLICE_MIN = 9

# Every `gh` call. Measured 2026-10-20 from this machine: `gh run view --json jobs`
# 0.6-0.9 s, `gh api .../check-runs/<id>/annotations` 0.4-0.7 s. 30 s is over 30x
# the slowest; at the bound the call counts as failed and the caller says so.
GH_CALL_TIMEOUT_S = 30

# GitHub's own limit on annotations: 10 error and 10 warning annotations per step,
# 50 per job (docs.github.com, "Workflow commands for GitHub Actions", limits). One
# page of 100 therefore holds every annotation a job can carry.
ANNOTATIONS_PER_PAGE = 100

# The refusal GitHub writes on every job of a run it will not start for billing.
# Recorded verbatim from run 39493655874 (2026-10-20): "The job was not started
# because recent account payments have failed or your spending limit needs to be
# increased. Please check the 'Billing & plans' section in your settings". GitHub
# also words it as an account "locked due to a billing issue"; both say billing.
BILLING_REFUSAL = re.compile(
    r"job was not started because .*(payments? ha(?:ve|s) failed|spending limit|billing)",
    re.IGNORECASE | re.DOTALL,
)
BILLING_LINE = ("GitHub Actions refused to start jobs: billing or spending limit; "
                "fix under Settings → Billing & plans")


def gh_json(args: list[str]) -> object | None:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             timeout=GH_CALL_TIMEOUT_S)
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


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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


# --- why a run did not succeed ---------------------------------------------------

def _first_line(text: object) -> str:
    return str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""


def _annotations_path(run_url: str, job_id: object) -> str:
    """The check-run annotations of one job. An Actions job id IS its check-run id.
    The repository comes from the run's own URL, so `--repo` and the cwd agree."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/actions/runs/", run_url or "")
    owner_repo = f"{m.group(1)}/{m.group(2)}" if m else "{owner}/{repo}"
    return f"repos/{owner_repo}/check-runs/{job_id}/annotations?per_page={ANNOTATIONS_PER_PAGE}"


def classify(view: dict, annotations_of) -> list[str]:
    """Reason lines for a completed run that did not succeed.

    `view` is `gh run view --json status,conclusion,url,jobs`; `annotations_of(job)`
    returns that job's check-run annotations (a list) or None when they could not be
    read. Pure, so recorded fixtures can drive it."""
    conclusion = str(view.get("conclusion") or "unknown")
    jobs = view.get("jobs") if isinstance(view.get("jobs"), list) else []
    failed = [j for j in jobs if str(j.get("conclusion") or "") in
              ("failure", "timed_out", "startup_failure", "action_required")]
    if not jobs:
        return [f"reason=no-jobs conclusion={conclusion}: GitHub ran no job (a workflow file "
                "GitHub could not start, or a run cancelled before any job); read the run page"]
    if not failed:
        names = ", ".join(sorted({str(j.get('conclusion') or j.get('status')) for j in jobs}))
        return [f"reason=no-failed-job conclusion={conclusion}: no job failed (job states: {names})"]

    refusal = None
    refused, unread, broken = 0, [], []
    for job in failed:
        notes = annotations_of(job)
        if notes is None:
            unread.append(job)
            continue
        messages = [str(a.get("message") or "") for a in notes
                    if isinstance(a, dict) and a.get("annotation_level") == "failure"]
        billing = [m for m in messages if BILLING_REFUSAL.search(m)]
        if billing:
            refused += 1
            refusal = refusal or _first_line(billing[0])
            continue
        steps = [str(s.get("name")) for s in (job.get("steps") or [])
                 if str(s.get("conclusion") or "") in ("failure", "timed_out")]
        line = f"job failed: {job.get('name')} ({job.get('conclusion')})"
        if steps:
            line += f", step: {', '.join(steps)}"
        broken.append([line, *(f"  annotation: {_first_line(m)}" for m in messages if _first_line(m))])

    out = []
    if refused:
        out.append(f"reason=billing {BILLING_LINE} (owner action; {refused} of {len(failed)} "
                   f"failed jobs were never started: \"{refusal}\")")
    if broken:
        out.append(f"reason=job-failure {len(broken)} failed job(s):")
        for lines in broken:
            out.extend(lines)
    if unread:
        out.append("reason=annotations-unread for " + ", ".join(str(j.get("name")) for j in unread)
                   + ": `gh api` failed; read `gh run view <id> --log-failed`")
    return out


def reason(run_id: str, repo: str | None) -> tuple[int, list[str]]:
    view = gh_json(["run", "view", run_id, "--json", "status,conclusion,url,jobs", *_repo(repo)])
    if not isinstance(view, dict):
        return 2, [f"reason=unread run_id={run_id}: `gh run view` failed"]
    if str(view.get("status")) != "completed":
        return 2, [f"reason=not-completed run_id={run_id} status={view.get('status')}"]
    url = str(view.get("url") or "")
    cache: dict[object, object] = {}

    def annotations_of(job: dict):
        key = job.get("databaseId")
        if key not in cache:
            got = gh_json(["api", _annotations_path(url, key)])
            cache[key] = got if isinstance(got, list) else None
        return cache[key]

    if str(view.get("conclusion")) == "success":
        return 0, ["reason=none: the run succeeded"]
    return 1, classify(view, annotations_of)


# --- the wait ---------------------------------------------------------------------

def await_run(run_id: str, repo: str | None, deadline_s: float, poll_s: float,
              slice_s: float | None = None, clock=time.monotonic, sleep=time.sleep,
              wall=time.time, explain=reason) -> tuple[int, str]:
    """Wait until the run completes, the run's own deadline passes, or the slice ends.

    The deadline counts from the run's `startedAt` (GitHub's `run_started_at`, which
    resets on a re-run), else its `createdAt`, else this call's start; so each slice
    of a foreground wait measures against the same bound."""
    start = clock()
    end = start + deadline_s
    slice_end = start + slice_s if slice_s else None
    status = "unknown"
    url = "-"
    anchored = False
    while True:
        view = gh_json(["run", "view", run_id, "--json", "status,conclusion,url,createdAt,startedAt",
                        *_repo(repo)])
        if isinstance(view, dict):
            status = str(view.get("status") or "unknown")
            url = str(view.get("url") or url)
            if not anchored:
                began = _epoch(view.get("startedAt")) or _epoch(view.get("createdAt"))
                if began is not None:
                    end = start + deadline_s - max(0.0, wall() - began)
                anchored = True
            if status == "completed":
                conclusion = str(view.get("conclusion") or "unknown")
                line = f"conclusion={conclusion} run_id={run_id} url={url}"
                if conclusion == "success":
                    return 0, line
                _, why = explain(run_id, repo)
                return 1, "\n".join([line, *why])
        now = clock()
        if now >= end:
            return 5, (f"release_run_await_deadline NOT CONCLUDED run_id={run_id} status={status} url={url} "
                       f"after={deadline_s / 60:g}min since the run started: the run is still open and has no "
                       "verdict; it is not done")
        if slice_end is not None and now >= slice_end:
            return 6, (f"release_run_still_running run_id={run_id} status={status} url={url} "
                       f"{max(0.0, end - now) / 60:.0f}min left of the run's {deadline_s / 60:g}min bound: "
                       "run this same command again")
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
    a.add_argument("--slice-min", type=float,
                   help=f"end this call after this long with exit 6 (a foreground wait: {FOREGROUND_SLICE_MIN})")
    a.add_argument("--poll-s", type=float, default=AWAIT_POLL_S)
    r = sub.add_parser("reason")
    r.add_argument("run_id")
    r.add_argument("--repo")
    args = ap.parse_args(argv)
    if args.cmd == "find":
        code, line = find(args.workflow, args.sha, args.repo, args.deadline_s, args.poll_s)
    elif args.cmd == "await":
        slice_s = args.slice_min * 60 if args.slice_min else None
        code, line = await_run(args.run_id, args.repo, args.deadline_min * 60, args.poll_s, slice_s)
    else:
        code, lines = reason(args.run_id, args.repo)
        line = "\n".join(lines)
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
