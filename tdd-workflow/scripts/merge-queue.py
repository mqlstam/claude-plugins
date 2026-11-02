#!/usr/bin/env python3
"""Whether a branch lands through GitHub's merge queue, and the wait for a queued PR.

    python3 merge-queue.py mode [--repo owner/name] [--branch B]
        Exit 0, `merge_queue=required branch=<B>`: the branch's active rules include a
        `merge_queue` rule (REST `GET /repos/{o}/{r}/rules/branches/{B}`), so a PR lands
        by `gh pr merge`, which then adds it to the queue, and a push to it is refused.
        Exit 1, `merge_queue=none branch=<B>`: land directly, as before.
        Exit 2: GitHub could not be read. The skills STOP and report: a guess of
        "direct" in a queue repo pushes into a refusal, a guess of "queue" in a direct
        repo waits for a queue that never merges.
        <B> defaults to the repository's default branch.

    python3 merge-queue.py await <PR> --head <SHA> --slice-min <m> [--until <epoch>] [--repo owner/name]
        Waits until the queue has merged the PR, in foreground slices, the way
        release-run.py `await` waits for a run. Exit status and the last line:
          0  `merged=<merge commit> pr=<PR>`: the queue landed it; <merge commit> is
             the commit on main.
          3  `removed pr=<PR> state=<…>`: the PR left the queue without merging (its
             merge group failed or timed out, a conflict with main, a manual dequeue,
             or the PR was closed). Followed, when GitHub has it, by the merge group's
             workflow runs of this PR that did not succeed (`merge_group_run=…`).
             A group that failed for ANOTHER queued PR is rebuilt by GitHub with
             this PR still queued: that is not a removal and the wait goes on.
          4  `head_moved pr=<PR> head=<…>`: the PR's head is not <SHA>; someone pushed.
          5  `merge_queue_await_deadline NOT MERGED …`: the bound passed (below); the
             line names the entry's state and position. It is not done.
          6  `merge_queue_still_queued … --until <epoch>`: the slice ended, the bound
             has not: run the same command again with that `--until`.
          2  usage, or the PR or its queue could not be read at the first call.
        A PR not in the queue yet but with auto-merge enabled (it waits for its own
        required check before GitHub adds it) is waited for too.

    python3 merge-queue.py dequeue <PR> [--repo owner/name]
        Takes the PR out of the queue (GraphQL `dequeuePullRequest`) and turns off a
        pending auto-merge (`disablePullRequestAutoMerge`), so a push to its branch
        cannot land under the old enqueue. Not a release: it un-queues. Exit 0 did it,
        1 nothing was queued or pending, 2 `gh` failed.

Why the wait is sliced (release-run.py, "a release wait that cannot outlive its
call"): Claude Code stops a foreground Bash call at 10 minutes and a background one
dies with its session (run 39507708450, 2026-10-20). So `await` refuses to run without
`--slice-min` (at most `FOREGROUND_SLICE_MIN`), and the bound is an absolute `--until`
the first call derives and prints, so every slice measures against the same bound.

What GitHub does that this relies on (docs.github.com, merge queue and GraphQL
reference, read 2026-11-02, unless marked): `isInMergeQueue`, `mergeQueueEntry
{state position}` and `autoMergeRequest` on the PR; a failing required check in the
group removes the PR and turns its auto-merge off; a push to a queued PR evicts it
(community reports, not documented).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time

# One slice of a foreground wait: the same 9 min as release-run.py's
# FOREGROUND_SLICE_MIN (Claude Code stops a foreground Bash call at 10 min; a slice
# returns by 9 min plus one `gh` call).
FOREGROUND_SLICE_MIN = 9

# One GraphQL read per poll. Mergeability polls every 5 s (wait-mergeable.py); a queued
# PR waits for its merge group's proof, minutes long, so 30 s makes 6x fewer reads per
# minute (120 an hour, far inside GitHub's 5,000) and still notices a merge well under
# a minute after it, against a proof that takes several.
POLL_S = 30

# The longest a `gh` call may take: the same 30 s release-run.py measured against
# (`gh` reads of 0.4-0.9 s from this machine, 2026-10-20). Near a stop a call gets less.
GH_CALL_TIMEOUT_S = 30

# GitHub's dispatch of a merge group: the queue creates the group branch and starts its
# workflow runs. Reasoned, not measured: GitHub registers a run 3-22 s after its event
# (release-run.py FIND_DEADLINE_S, 45 tag pushes), and five minutes leaves room for a
# queue that waits to fill a group (`minimumEntriesToMergeWaitTime`) on a quiet repo.
DISPATCH_GRACE_S = 5 * 60

# Read in one page: GitHub's REST maximum per page (docs.github.com, "Using pagination
# in the REST API"). A queue's newest merge-group runs, of every workflow, are on it.
RUNS_PER_PAGE = 100

# The repository rules of one branch fit one page of the same REST maximum: a ruleset
# holds a handful of rule types, and a branch rarely matches more than a few rulesets.
RULES_PER_PAGE = 100

NOT_SUCCEEDED = ("failure", "timed_out", "cancelled", "startup_failure", "action_required")

PR_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){id state headRefOid baseRefName isInMergeQueue "
    "mergeCommit{oid} autoMergeRequest{enabledAt} "
    "mergeQueueEntry{state position enqueuedAt estimatedTimeToMerge}}}}"
)
QUEUE_QUERY = (
    "query($owner:String!,$name:String!,$branch:String!){repository(owner:$owner,name:$name){"
    "mergeQueue(branch:$branch){configuration{checkResponseTimeout mergeMethod maximumEntriesToBuild} "
    "entries(first:1){totalCount}}}}"
)
DEQUEUE_MUTATION = (
    "mutation($id:ID!){dequeuePullRequest(input:{id:$id}){mergeQueueEntry{state position}}}"
)
DISABLE_AUTO_MERGE_MUTATION = (
    "mutation($id:ID!){disablePullRequestAutoMerge(input:{pullRequestId:$id}){pullRequest{number}}}"
)


def _call_timeout(stop: float, now: float) -> float:
    """release-run.py's rule: a call may start up to the stop and run one call past it."""
    if now > stop:
        return 0.0
    return min(GH_CALL_TIMEOUT_S, stop + GH_CALL_TIMEOUT_S - now)


def gh_json(args: list[str], timeout: float = GH_CALL_TIMEOUT_S) -> object | None:
    """`gh <args>` parsed as JSON, or None when it failed, timed out or had no time."""
    if timeout <= 0:
        return None
    try:
        out = subprocess.run(["gh", *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout.decode("utf-8"))
    except ValueError:
        return None


def _graphql(query: str, fields: dict[str, str], ints: dict[str, str] | None = None,
             timeout: float = GH_CALL_TIMEOUT_S) -> dict | None:
    """The `data` of a GraphQL request, None when it failed or GitHub answered errors."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in fields.items():
        args += ["-f", f"{key}={value}"]
    for key, value in (ints or {}).items():
        args += ["-F", f"{key}={value}"]
    got = gh_json(args, timeout)
    if not isinstance(got, dict) or got.get("errors") or not isinstance(got.get("data"), dict):
        return None
    return got["data"]


def repo_info(repo: str | None, timeout: float = GH_CALL_TIMEOUT_S) -> tuple[str, str] | None:
    """(owner/name, default branch) of `repo`, or of the checkout gh reads."""
    got = gh_json(["repo", "view", *([repo] if repo else []), "--json", "nameWithOwner,defaultBranchRef"],
                  timeout)
    if not isinstance(got, dict) or not got.get("nameWithOwner"):
        return None
    return str(got["nameWithOwner"]), str((got.get("defaultBranchRef") or {}).get("name") or "")


def _split(slug: str) -> tuple[str, str]:
    owner, name = slug.split("/", 1)
    return owner, name


# --- mode -------------------------------------------------------------------------

def mode(repo: str | None, branch: str | None) -> tuple[int, str]:
    info = repo_info(repo)
    if info is None:
        return 2, "merge_queue=unknown: `gh repo view` failed; STOP and report, never guess the path"
    slug, default = info
    branch = branch or default
    if not branch:
        return 2, f"merge_queue=unknown repo={slug}: no default branch read; pass --branch"
    rules = gh_json(["api", f"repos/{slug}/rules/branches/{branch}?per_page={RULES_PER_PAGE}"])
    if not isinstance(rules, list):
        return 2, (f"merge_queue=unknown repo={slug} branch={branch}: the branch's rules could not be "
                   "read; STOP and report, never guess the path")
    if any(isinstance(r, dict) and r.get("type") == "merge_queue" for r in rules):
        return 0, f"merge_queue=required branch={branch} repo={slug}"
    return 1, f"merge_queue=none branch={branch} repo={slug}"


# --- await ------------------------------------------------------------------------

def read_pr(slug: str, pr: str, timeout: float) -> dict | None:
    owner, name = _split(slug)
    data = _graphql(PR_QUERY, {"owner": owner, "name": name}, {"number": pr}, timeout)
    node = ((data or {}).get("repository") or {}).get("pullRequest")
    return node if isinstance(node, dict) else None


def read_queue(slug: str, branch: str, timeout: float) -> dict | None:
    owner, name = _split(slug)
    data = _graphql(QUEUE_QUERY, {"owner": owner, "name": name, "branch": branch}, None, timeout)
    queue = ((data or {}).get("repository") or {}).get("mergeQueue")
    return queue if isinstance(queue, dict) else None


def derive_until(view: dict, queue: dict, wall_now: float) -> float | None:
    """The absolute bound of the wait, read once at its first call.

    Worst case, every group ahead of this PR runs to the queue's check timeout once
    before this PR's own group is built, and that group runs to the timeout too:
    (position) x checkResponseTimeout + one checkResponseTimeout for this entry, plus
    DISPATCH_GRACE_S for GitHub to start the groups. The position is read once, here,
    and never re-derived: a queue that moves only shortens the real wait. `position`
    counts from 1 at the head (if it counts from 0 the bound is one timeout tighter
    and still covers every group ahead: GitHub does not say which). A PR not in the
    queue yet (auto-merge waits for its own required check) joins behind every entry
    now queued, and its own check gets one more timeout."""
    timeout_min = (queue.get("configuration") or {}).get("checkResponseTimeout")
    if not isinstance(timeout_min, int) or timeout_min <= 0:
        return None
    entry = view.get("mergeQueueEntry") or {}
    if isinstance(entry.get("position"), int):
        ahead = max(0, entry["position"])
        own_check = 0
    else:
        ahead = int(((queue.get("entries") or {}).get("totalCount")) or 0)
        own_check = 1
    timeout_s = timeout_min * 60  # GraphQL: "The amount of time in minutes"
    return wall_now + (ahead + 1 + own_check) * timeout_s + DISPATCH_GRACE_S


def _same(head: str, other: str) -> bool:
    return bool(other) and (other.startswith(head) or head.startswith(other))


def failing_group_runs(slug: str, pr: str, base: str, timeout: float) -> list[str]:
    """The newest merge group of this PR's workflow runs that did not succeed, one
    line each; or one line saying none could be read or none failed."""
    prefix = f"gh-readonly-queue/{base}/pr-{pr}-"
    runs = gh_json(["api", f"repos/{slug}/actions/runs?event=merge_group&per_page={RUNS_PER_PAGE}",
                    "--jq", "[.workflow_runs[] | {name, head_branch, head_sha, status, conclusion, "
                            "html_url, created_at}]"], timeout)
    if not isinstance(runs, list):
        return [f"merge_group_runs=unread: read `gh run list --event merge_group -R {slug}`"]
    mine = [r for r in runs if isinstance(r, dict) and str(r.get("head_branch") or "").startswith(prefix)]
    if not mine:
        return [f"merge_group_runs=none for {prefix}*: no group of this PR ran a workflow (a conflict "
                "with main, a timeout before dispatch, or a manual dequeue)"]
    newest = max(mine, key=lambda r: str(r.get("created_at") or ""))
    group = [r for r in mine if r.get("head_sha") == newest.get("head_sha")]
    bad = [r for r in group if r.get("status") == "completed" and r.get("conclusion") in NOT_SUCCEEDED]
    if not bad:
        states = ", ".join(sorted({f"{r.get('name')}={r.get('conclusion') or r.get('status')}" for r in group}))
        return [f"merge_group_runs=no-failure group={newest.get('head_branch')} ({states}): GitHub removed "
                "the PR for another reason (a check that never reported, a conflict, a dequeue)"]
    return [f"merge_group_run={r.get('html_url')} name={r.get('name')} conclusion={r.get('conclusion')}"
            for r in sorted(bad, key=lambda r: str(r.get("name")))]


def await_merge(pr: str, head: str, repo: str | None, slice_s: float, until: float | None,
                poll_s: float = POLL_S, deadline_s: float | None = None,
                clock=time.monotonic, sleep=time.sleep, wall=time.time) -> tuple[int, str]:
    start = clock()
    slice_end = start + slice_s
    info = repo_info(repo, _call_timeout(slice_end, clock()))
    if info is None:
        return 2, "merge_queue_await unread: `gh repo view` failed; run the same command again"
    slug = info[0]
    state, position, entry_state = "unknown", "-", "-"
    while True:
        view = read_pr(slug, pr, _call_timeout(slice_end, clock()))
        if view is not None:
            state = str(view.get("state") or "unknown")
            entry = view.get("mergeQueueEntry") or {}
            entry_state = str(entry.get("state") or "-")
            position = str(entry.get("position") if entry.get("position") is not None else "-")
            current = str(view.get("headRefOid") or "")
            if not _same(head, current):
                return 4, (f"head_moved pr={pr} head={current or '-'} (expected {head}) state={state}: "
                           "someone pushed; STOP and report the unreviewed head")
            if state == "MERGED":
                return 0, f"merged={(view.get('mergeCommit') or {}).get('oid') or '-'} pr={pr}"
            pending = bool(view.get("isInMergeQueue")) or view.get("autoMergeRequest") is not None
            if state != "OPEN" or not pending:
                lines = [f"removed pr={pr} state={state} queued=no auto_merge=off"]
                lines += failing_group_runs(slug, pr, str(view.get("baseRefName") or ""),
                                            _call_timeout(slice_end, clock()))
                return 3, "\n".join(lines)
            if until is None:
                # Still waiting: the bound, read once (a verdict above needs none).
                if deadline_s is not None:
                    until = wall() + deadline_s
                else:
                    queue = read_queue(slug, str(view.get("baseRefName") or ""), _call_timeout(slice_end, clock()))
                    until = derive_until(view, queue, wall()) if queue else None
                    if until is None:
                        return 2, (f"merge_queue_await unread pr={pr}: the queue of "
                                   f"{view.get('baseRefName')} or its checkResponseTimeout could not be read")
        now = clock()
        if until is not None and wall() >= until:
            return 5, (f"merge_queue_await_deadline NOT MERGED pr={pr} state={state} entry={entry_state} "
                       f"position={position}: the bound derived from the queue's check timeout passed; "
                       "the PR is not merged and this is not done")
        if now >= slice_end:
            if until is None:
                return 2, f"merge_queue_await unread pr={pr}: GitHub did not answer within the slice"
            left = max(0.0, until - wall())
            return 6, (f"merge_queue_still_queued pr={pr} state={state} entry={entry_state} "
                       f"position={position} {left / 60:.0f}min left of the bound: run this same "
                       f"command again with --until {until:.0f}")
        pause = min(poll_s, slice_end - now)
        if until is not None:
            pause = min(pause, max(0.0, until - wall()))
        sleep(max(pause, 0.0))


# --- dequeue ----------------------------------------------------------------------

def dequeue(pr: str, repo: str | None) -> tuple[int, str]:
    info = repo_info(repo)
    if info is None:
        return 2, "dequeue failed: `gh repo view` failed"
    slug = info[0]
    view = read_pr(slug, pr, GH_CALL_TIMEOUT_S)
    if view is None or not view.get("id"):
        return 2, f"dequeue failed: PR {pr} of {slug} could not be read"
    done = []
    if view.get("isInMergeQueue"):
        if _graphql(DEQUEUE_MUTATION, {"id": str(view["id"])}) is None:
            return 2, f"dequeue failed: `dequeuePullRequest` for PR {pr} answered an error"
        done.append("removed from the merge queue")
    if view.get("autoMergeRequest") is not None:
        if _graphql(DISABLE_AUTO_MERGE_MUTATION, {"id": str(view["id"])}) is None:
            return 2, f"dequeue failed: `disablePullRequestAutoMerge` for PR {pr} answered an error"
        done.append("auto-merge turned off")
    if not done:
        return 1, f"dequeued=nothing pr={pr}: it was neither queued nor waiting to be"
    return 0, f"dequeued pr={pr}: " + ", ".join(done) + "; land it again with `gh pr merge` once pushed"


# --- entry point ------------------------------------------------------------------

def _bound(text: str) -> float:
    value = float(text)
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(f"{text!r} is not a finite number above 0")
    return value


def _pr(text: str) -> str:
    if not re.fullmatch(r"\d+", text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a PR number")
    return text


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mode")
    m.add_argument("--repo")
    m.add_argument("--branch")
    a = sub.add_parser("await")
    a.add_argument("pr", type=_pr)
    a.add_argument("--head", required=True)
    a.add_argument("--repo")
    a.add_argument("--slice-min", type=float,
                   help=f"required: end this call after this long with exit 6 (at most {FOREGROUND_SLICE_MIN})")
    a.add_argument("--until", type=_bound, help="the absolute bound (epoch s) the first call printed")
    a.add_argument("--deadline-s", type=_bound, help="override the derived bound (tests)")
    a.add_argument("--poll-s", type=_bound, default=POLL_S)
    d = sub.add_parser("dequeue")
    d.add_argument("pr", type=_pr)
    d.add_argument("--repo")
    args = ap.parse_args(argv)
    if args.cmd == "await" and not (args.slice_min and 0 < args.slice_min <= FOREGROUND_SLICE_MIN):
        # As in release-run.py: a wait without a slice outlives the foreground Bash call
        # that holds it, and a background one dies with its session.
        a.error(f"--slice-min is required, above 0 and at most {FOREGROUND_SLICE_MIN}: the queue's verdict "
                f"is read in the foreground, in slices that each fit one Bash call (--slice-min "
                f"{FOREGROUND_SLICE_MIN}); on exit 6 run the same command again with the --until it prints")
    if args.cmd == "mode":
        code, line = mode(args.repo, args.branch)
    elif args.cmd == "await":
        code, line = await_merge(args.pr, args.head, args.repo, args.slice_min * 60, args.until,
                                 args.poll_s, args.deadline_s)
    else:
        code, line = dequeue(args.pr, args.repo)
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
