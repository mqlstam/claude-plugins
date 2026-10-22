#!/usr/bin/env python3
"""Find the Actions run a release started, wait for its verdict, and say why it failed.

    python3 release-run.py find <workflow-file> <sha> [--repo owner/name]
        Prints `run_id=<id> url=<url>` once GitHub has registered the run for <sha>.

    python3 release-run.py await <run-id> --deadline-min <n> --slice-min <m> [--repo owner/name]
        Exits when the run is completed and prints `conclusion=<conclusion> run_id=<id>
        url=<url>`, read from `gh run view --json status,conclusion`, never from a
        watch's exit code. A conclusion other than `success` is followed by the reason
        (see `reason`). `--deadline-min` counts from the run's own start, so a session
        that waits in slices of `--slice-min` (required, at most `FOREGROUND_SLICE_MIN`)
        re-runs the same command and the bound stays the run's, not the slice's.

    python3 release-run.py reason <run-id> [--repo owner/name]
        Why a completed run did not succeed: each failed job, its failed steps and its
        check-run annotations. A run GitHub refused to start for billing is named in
        one line as the owner's action.

Every command returns by its own stop plus one `gh` call, however slowly GitHub
answers: `find` stops at its deadline, `await` at the run's deadline or the slice's
end (whichever comes first) and `reason` after one foreground slice. No wait starts
past the stop, and every `gh` call's timeout is what is left of the stop plus one call
(`_call_timeout`), so a hung or failing API cannot carry a command past it. A failed
run's reason reads share the same stop: what they could not read by then is named,
and `reason` reads it again. After the last call the command only parses what `gh`
returned, linearly and capped at `GH_MAX_RESPONSE_BYTES` per call, so it still fits
the one foreground Bash call the slice is sized for (`FOREGROUND_SLICE_MIN`).

Why the session waits in the foreground (owner, 2026-10-20). Until then the release
skills started `await` in the background and ended their turn, to save the ~93 minutes
of session time a day a foreground watch had held (devstudy report, 2026-10-07). On
2026-10-20 a staging release (run 39493655874) failed 7 s after its dispatch because
GitHub Actions refused every job for billing, and nobody noticed: the T3 thread that
started it had settled 2 s before. It was the third billing refusal in 8 days. A
release is now watched to its end inside the turn, in slices that fit one foreground
Bash call (`FOREGROUND_SLICE_MIN`). The same night it happened again: run 39507708450's
`await` was started in the background at 22:23:11Z, the T3 thread settled on its PR's
merge when the turn ended, and Claude Code killed the wait at 22:23:18Z (its output file
reads `[killed]`). The run failed at 22:45:26Z, and nobody saw it until 08:12Z. So
`await` itself refuses to run without `--slice-min` (exit 2), however it is spelled, and
the plugin's Bash guard (bash-shape-guard.py, rule 3) refuses, before it starts, any
command that names this script's `await` and runs in the background or as a job (`&`).

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
import math
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
# at its `timeout`, at most 600000 ms (10 min). A slice of 9 returns by 9 min plus one
# `gh` call (`GH_CALL_TIMEOUT_S`), which holds the last poll and a failed run's reason
# reads; that leaves half a minute for the interpreter. Should the tool still stop the
# command, nothing is lost: the verdict lives in the API, and the next slice reads
# a completed run on its first poll.
FOREGROUND_SLICE_MIN = 9

# The longest a `gh` call may take. Measured 2026-10-20 from this machine: `gh run view
# --json jobs` 0.6-0.9 s, `gh api .../check-runs/<id>/annotations` 0.4-0.7 s. 30 s is
# over 30x the slowest; at the bound the call counts as failed and the caller says so.
# Near a command's stop a call gets less (`_call_timeout`).
GH_CALL_TIMEOUT_S = 30

# GitHub's own limit on annotations: 10 error and 10 warning annotations per step,
# 50 per job (docs.github.com, "Workflow commands for GitHub Actions", limits). One
# page of 100 therefore holds every annotation a job can carry.
ANNOTATIONS_PER_PAGE = 100

# The most a `gh` answer may hold before it counts as failed. The largest answer read
# here is one page of annotations: at most 50 per job (above), each with a `message`
# and a `raw_details` of at most 64 KB apiece (docs.github.com, REST "Check runs",
# read 2026-10-22), so ~6.6 MB with its JSON. 16 MiB leaves room for the other fields
# and JSON escaping; parsing it takes about a second, so a
# full page cannot carry a command past its foreground call (Codex review,
# 2026-10-22: 50 annotations of 65,520 bytes parsed in ~0.5 s).
GH_MAX_RESPONSE_BYTES = 16 * 1024 * 1024

# The refusal GitHub writes on every job of a run it will not start for billing.
# Recorded verbatim from run 39493655874 (2026-10-20): "The job was not started
# because recent account payments have failed or your spending limit needs to be
# increased. Please check the 'Billing & plans' section in your settings". GitHub
# also words it as an account "locked due to a billing issue"; both say billing.
BILLING_REFUSAL_PREFIX = re.compile(r"job was not started because ", re.IGNORECASE)
BILLING_REFUSAL_REASON = re.compile(r"payments? ha(?:ve|s) failed|spending limit|billing", re.IGNORECASE)


def is_billing_refusal(message: str) -> bool:
    """GitHub refusing to start a job for billing: a reason word after the refusal.

    Two linear scans, not one `prefix .*(reason)` regex: that one backtracks
    quadratically over a repeated prefix (50 annotations of it repeated 1,000 times
    took 99.9 s inside the await's bound; Codex review, 2026-10-22). A reason after
    any occurrence of the prefix is a reason after its first, so the meaning holds.
    Both scans match case-insensitively on the message itself, so an offset is never
    taken from a lowered copy whose length differs (`İ` lowers to two characters)."""
    refusal = BILLING_REFUSAL_PREFIX.search(message)
    return refusal is not None and BILLING_REFUSAL_REASON.search(message, refusal.end()) is not None
BILLING_LINE = ("GitHub Actions refused to start jobs: billing or spending limit; "
                "fix under Settings → Billing & plans")


def _call_timeout(stop: float, now: float) -> float:
    """The timeout of a `gh` call made at `now` by a command that stops at `stop`.

    A call may start up to the stop and run one call's length past it, never more, so
    the command returns by `stop + GH_CALL_TIMEOUT_S` whatever GitHub does. From that
    moment on it is zero or less, and `gh_json` makes no call. Past the stop it is zero
    too: a call that ran past the stop must not let the next one start there. A call
    exactly at the stop still runs, so the last status read can see the run finish."""
    if now > stop:
        return 0.0
    return min(GH_CALL_TIMEOUT_S, stop + GH_CALL_TIMEOUT_S - now)


def gh_json(args: list[str], timeout: float = GH_CALL_TIMEOUT_S) -> object | None:
    """`gh <args>` parsed as JSON, or None when it failed, timed out or had no time.

    On a timeout `subprocess.run` kills `gh` and waits for it alone (POSIX), so a
    child of `gh` that still holds the pipe cannot keep this call open."""
    if timeout <= 0:
        return None
    try:
        out = subprocess.run(["gh", *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # The cap counts bytes, as read, before any decoding.
    if out.returncode != 0 or len(out.stdout) > GH_MAX_RESPONSE_BYTES:
        return None
    try:
        return json.loads(out.stdout.decode("utf-8"))
    except ValueError:
        return None


def _repo(repo: str | None) -> list[str]:
    return ["--repo", repo] if repo else []


def _epoch(stamp: object) -> float | None:
    """Seconds since the Unix epoch, or None when there is no time.

    gh exports a null `run_started_at` as Go's zero time, `0001-01-01T00:00:00Z`: its
    `startedAt` field is the raw value, not the `StartedTime()` that falls back to the
    creation time (cli/cli v2.102.1, pkg/cmd/run/shared/shared.go). GitHub has no time
    before the epoch, so one is no time."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        seconds = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def find(workflow: str, sha: str, repo: str | None, deadline_s: float, poll_s: float,
         clock=time.monotonic, sleep=time.sleep) -> tuple[int, str]:
    end = clock() + deadline_s
    while True:
        runs = gh_json(["run", "list", "--workflow", workflow, "--commit", sha, "--limit", "20",
                        "--json", "databaseId,url,createdAt,event", *_repo(repo)],
                       timeout=_call_timeout(end, clock()))
        if isinstance(runs, list) and runs:
            newest = max(runs, key=lambda r: str(r.get("createdAt") or ""))
            return 0, f"run_id={newest.get('databaseId')} url={newest.get('url')}"
        now = clock()
        if now >= end:
            return 5, (f"release_run_find_deadline workflow={workflow} sha={sha[:12]} "
                       f"after={deadline_s:g}s: no run registered; read `gh run list --workflow {workflow}`")
        sleep(min(poll_s, end - now))


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
        billing = [m for m in messages if is_billing_refusal(m)]
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
                   + ": `gh api` failed or ran out of time; read `gh run view <id> --log-failed`")
    return out


def reason(run_id: str, repo: str | None, stop: float | None = None,
           clock=time.monotonic) -> tuple[int, list[str]]:
    """Read why a completed run did not succeed, by `stop` plus one `gh` call.

    `await` passes its own stop: the run's deadline or the slice's end, whichever is
    first. Run on its own, `reason` is one foreground Bash call as well, so it gets one
    slice."""
    if stop is None:
        stop = clock() + FOREGROUND_SLICE_MIN * 60
    view = gh_json(["run", "view", run_id, "--json", "status,conclusion,url,jobs", *_repo(repo)],
                   timeout=_call_timeout(stop, clock()))
    if not isinstance(view, dict):
        return 2, [f"reason=unread run_id={run_id}: `gh run view` failed or ran out of time; "
                   f"run `release-run.py reason {run_id}`"]
    if str(view.get("status")) != "completed":
        return 2, [f"reason=not-completed run_id={run_id} status={view.get('status')}"]
    url = str(view.get("url") or "")
    cache: dict[object, object] = {}

    def annotations_of(job: dict):
        key = job.get("databaseId")
        if key not in cache:
            got = gh_json(["api", _annotations_path(url, key)], timeout=_call_timeout(stop, clock()))
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
    resets on a re-run), else its `createdAt`, else this call's start, read again on
    every poll: a re-run attempt that starts during a slice moves the bound to its own
    start, so each slice of a foreground wait measures against the same bound. No poll or pause starts past
    the earlier of the two, and the call returns by it plus one `gh` call, a failed
    run's reason reads included: what they could not read by then is named, with the
    command that reads it (`reason`)."""
    start = clock()
    end = start + deadline_s
    # The slice's end; without a slice the run's deadline alone stops the call, and
    # that one moves when the run is anchored again.
    slice_end = start + slice_s if slice_s else math.inf
    status = "unknown"
    url = "-"
    anchor: float | None = None
    while True:
        view = gh_json(["run", "view", run_id, "--json", "status,conclusion,url,createdAt,startedAt",
                        *_repo(repo)], timeout=_call_timeout(min(end, slice_end), clock()))
        if isinstance(view, dict):
            status = str(view.get("status") or "unknown")
            url = str(view.get("url") or url)
            began = _epoch(view.get("startedAt")) or _epoch(view.get("createdAt"))
            if began is not None and began != anchor:
                # Anchored again whenever the start moves (a re-run attempt resets
                # `startedAt`; a queued run gains one), so the bound is the one a
                # fresh slice would read now. Both clocks read at the same instant:
                # what is left of the run's bound, never more than the whole bound.
                end = clock() + deadline_s - max(0.0, wall() - began)
                anchor = began
            if status == "completed":
                conclusion = str(view.get("conclusion") or "unknown")
                line = f"conclusion={conclusion} run_id={run_id} url={url}"
                if conclusion == "success":
                    return 0, line
                _, why = explain(run_id, repo, min(end, slice_end), clock)
                return 1, "\n".join([line, *why])
        now = clock()
        if now >= end:
            return 5, (f"release_run_await_deadline NOT CONCLUDED run_id={run_id} status={status} url={url} "
                       f"after={deadline_s / 60:g}min since the run started: the run is still open and has no "
                       "verdict; it is not done")
        if now >= slice_end:
            return 6, (f"release_run_still_running run_id={run_id} status={status} url={url} "
                       f"{max(0.0, end - now) / 60:.0f}min left of the run's {deadline_s / 60:g}min bound: "
                       "run this same command again")
        sleep(min(poll_s, min(end, slice_end) - now))


def _bound(text: str) -> float:
    """A number that bounds a wait: finite and above 0. `nan` compares false with
    everything, so a NaN deadline turned every `min` and `>=` in `await_run` into a
    pause of the whole poll interval past the slice (Codex review, 2026-10-22)."""
    value = float(text)
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(f"{text!r} is not a finite number above 0")
    return value


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("workflow")
    f.add_argument("sha")
    f.add_argument("--repo")
    f.add_argument("--deadline-s", type=_bound, default=FIND_DEADLINE_S)
    f.add_argument("--poll-s", type=_bound, default=FIND_POLL_S)
    a = sub.add_parser("await")
    a.add_argument("run_id")
    a.add_argument("--repo")
    a.add_argument("--deadline-min", type=_bound, required=True,
                   help="the workflow's critical path of job timeouts, derived by the caller")
    a.add_argument("--slice-min", type=float,
                   help=f"required: end this call after this long with exit 6 ({FOREGROUND_SLICE_MIN} fits "
                        "one foreground Bash call)")
    a.add_argument("--poll-s", type=_bound, default=AWAIT_POLL_S)
    r = sub.add_parser("reason")
    r.add_argument("run_id")
    r.add_argument("--repo")
    args = ap.parse_args(argv)
    if args.cmd == "await" and not (args.slice_min and 0 < args.slice_min <= FOREGROUND_SLICE_MIN):
        # The hard contract, here and not only in the skills or the Bash guard (which is
        # advisory and cannot read every shell spelling): a wait without a slice
        # outlives the foreground Bash call that holds it, and a background or detached
        # one dies with its session (run 39507708450, 2026-10-20). With the slice capped
        # at one foreground call, however the command is spelled or started, an await
        # returns within one slice plus one `gh` call, so a lost one costs at most one
        # slice of verdict and the skill's next slice reads it.
        a.error(f"--slice-min is required, above 0 and at most {FOREGROUND_SLICE_MIN}: the verdict is "
                f"read in the foreground, in slices that each fit one Bash call (--slice-min "
                f"{FOREGROUND_SLICE_MIN}); on exit 6 run the same command again")
    if args.cmd == "find":
        code, line = find(args.workflow, args.sha, args.repo, args.deadline_s, args.poll_s)
    elif args.cmd == "await":
        code, line = await_run(args.run_id, args.repo, args.deadline_min * 60, args.poll_s,
                               args.slice_min * 60)
    else:
        code, lines = reason(args.run_id, args.repo)
        line = "\n".join(lines)
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
