"""Tests for merge-queue.py and wait-mergeable.py's --queue mode (slice 989)."""
import importlib.util
import io
import os
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mq = load("merge_queue", "merge-queue.py")
wm = load("wait_mergeable_q", "wait-mergeable.py")

HEAD = "abc1234def"
M = "f" * 40
WALL0 = 1_800_000_000.0


class Clock:
    """One clock for monotonic and wall time, advanced by sleep."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def wall(self):
        return WALL0 + self.t

    def sleep(self, s):
        self.t += s


def pr(state="OPEN", head=HEAD, queued=True, auto=False, position=2, entry="QUEUED", merge=None):
    return {"id": "PR_1", "state": state, "headRefOid": head, "baseRefName": "main",
            "isInMergeQueue": queued, "mergeCommit": {"oid": merge} if merge else None,
            "autoMergeRequest": {"enabledAt": "2026-11-02T10:00:00Z"} if auto else None,
            "mergeQueueEntry": {"state": entry, "position": position} if queued else None}


def queue(timeout_min=60, total=3):
    return {"configuration": {"checkResponseTimeout": timeout_min, "mergeMethod": "SQUASH"},
            "entries": {"totalCount": total}}


class FakeGh:
    """What `gh` answers, routed by the call; `prs` is the sequence of PR reads."""

    def __init__(self, prs, queue_answer=None, runs=None, rules=None, repo=("o/r", "main"),
                 mutation_ok=True):
        self.prs = list(prs)
        self.queue = queue_answer if queue_answer is not None else queue()
        self.runs = runs
        self.rules = rules
        self.repo = repo
        self.mutation_ok = mutation_ok
        self.calls = []

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        if args[:2] == ["repo", "view"]:
            return None if self.repo is None else {"nameWithOwner": self.repo[0],
                                                   "defaultBranchRef": {"name": self.repo[1]}}
        if args[:2] == ["api", "graphql"]:
            query = args[3]
            if "mutation" in query:
                return {"data": {"x": {}}} if self.mutation_ok else {"errors": [{"message": "no"}]}
            if "mergeQueue(branch" in query:
                return {"data": {"repository": {"mergeQueue": self.queue}}}
            view = self.prs.pop(0) if len(self.prs) > 1 else self.prs[0]
            return None if view is None else {"data": {"repository": {"pullRequest": view}}}
        if args[0] == "api" and "/rules/branches/" in args[1]:
            return self.rules
        if args[0] == "api" and "actions/runs?event=merge_group" in args[1]:
            return self.runs
        raise AssertionError(f"unexpected gh call {args}")


def run_await(gh, slice_s=540, until=None, deadline_s=None, poll_s=30):
    clock = Clock()
    with unittest.mock.patch.object(mq, "gh_json", gh):
        result = mq.await_merge("12", HEAD, None, slice_s, until, poll_s, deadline_s,
                                clock=clock.now, sleep=clock.sleep, wall=clock.wall)
    return result, clock.t


class ModeTest(unittest.TestCase):
    def mode(self, gh, branch=None):
        with unittest.mock.patch.object(mq, "gh_json", gh):
            return mq.mode(None, branch)

    def test_a_merge_queue_rule_is_required(self):
        gh = FakeGh([None], rules=[{"type": "pull_request"}, {"type": "merge_queue", "parameters": {}}])
        self.assertEqual(self.mode(gh), (0, "merge_queue=required branch=main repo=o/r"))
        self.assertIn("repos/o/r/rules/branches/main?per_page=100", gh.calls[1][1])

    def test_no_queue_rule_is_direct(self):
        gh = FakeGh([None], rules=[{"type": "required_status_checks"}])
        self.assertEqual(self.mode(gh, "release")[0], 1)
        self.assertIn("rules/branches/release", gh.calls[1][1])
        self.assertEqual(self.mode(FakeGh([None], rules=[]))[0], 1)

    def test_unreadable_github_is_never_a_guess(self):
        code, line = self.mode(FakeGh([None], rules=None))
        self.assertEqual(code, 2)
        self.assertIn("never guess", line)
        self.assertEqual(self.mode(FakeGh([None], repo=None))[0], 2)


class AwaitTest(unittest.TestCase):
    def test_merged_names_the_merge_commit(self):
        (code, line), waited = run_await(FakeGh([pr(), pr(), pr(state="MERGED", queued=False, merge=M)]))
        self.assertEqual((code, line, waited), (0, f"merged={M} pr=12", 60))

    def test_auto_merge_waiting_for_its_own_check_keeps_waiting(self):
        gh = FakeGh([pr(queued=False, auto=True), pr(), pr(state="MERGED", queued=False, merge=M)])
        (code, _), _ = run_await(gh)
        self.assertEqual(code, 0)

    def test_removed_from_the_queue_names_the_failing_group_run(self):
        runs = [
            {"name": "Merge result proof", "head_branch": "gh-readonly-queue/main/pr-12-aaa", "head_sha": "s1",
             "status": "completed", "conclusion": "failure", "html_url": "https://x/runs/1",
             "created_at": "2026-11-02T10:00:00Z"},
            {"name": "Merge result proof", "head_branch": "gh-readonly-queue/main/pr-12-bbb", "head_sha": "s2",
             "status": "completed", "conclusion": "failure", "html_url": "https://x/runs/2",
             "created_at": "2026-11-02T11:00:00Z"},
            {"name": "Lint", "head_branch": "gh-readonly-queue/main/pr-12-bbb", "head_sha": "s2",
             "status": "completed", "conclusion": "success", "html_url": "https://x/runs/3",
             "created_at": "2026-11-02T11:00:01Z"},
            {"name": "Merge result proof", "head_branch": "gh-readonly-queue/main/pr-120-ccc", "head_sha": "s3",
             "status": "completed", "conclusion": "failure", "html_url": "https://x/runs/4",
             "created_at": "2026-11-02T12:00:00Z"},
        ]
        (code, line), _ = run_await(FakeGh([pr(), pr(queued=False)], runs=runs))
        self.assertEqual(code, 3)
        lines = line.splitlines()
        self.assertEqual(lines[0], "removed pr=12 state=OPEN queued=no auto_merge=off")
        self.assertEqual(lines[1:], ["merge_group_run=https://x/runs/2 name=Merge result proof conclusion=failure"],
                         "the newest group of THIS PR only (pr-120 is another PR)")

    def test_removed_without_a_readable_run_says_so(self):
        (code, line), _ = run_await(FakeGh([pr(queued=False)], runs=None))
        self.assertEqual(code, 3)
        self.assertIn("merge_group_runs=unread", line)
        (code, line), _ = run_await(FakeGh([pr(queued=False)], runs=[]))
        self.assertIn("merge_group_runs=none", line)

    def test_a_closed_pr_is_removed(self):
        (code, line), _ = run_await(FakeGh([pr(state="CLOSED", queued=False)], runs=[]))
        self.assertEqual(code, 3)
        self.assertIn("state=CLOSED", line)

    def test_a_moved_head_stops_the_wait(self):
        (code, line), _ = run_await(FakeGh([pr(head="0ther")]))
        self.assertEqual(code, 4)
        self.assertIn("head_moved pr=12 head=0ther", line)

    def test_the_slice_ends_with_a_resumable_bound(self):
        (code, line), waited = run_await(FakeGh([pr()]), slice_s=90)
        self.assertEqual((code, waited), (6, 90))
        # Position 2 behind a 60 min timeout: (2 + 1) x 60 min + 5 min grace.
        until = WALL0 + 3 * 3600 + 300
        self.assertIn(f"--until {until:.0f}", line)
        self.assertIn("merge_queue_still_queued pr=12 state=OPEN entry=QUEUED position=2", line)
        # The next slice carries the same bound and ends at it with exit 5.
        (code, line), waited = run_await(FakeGh([pr()]), until=WALL0 + 120)
        self.assertEqual((code, waited), (5, 120))
        self.assertIn("merge_queue_await_deadline NOT MERGED pr=12 state=OPEN entry=QUEUED position=2", line)

    def test_a_pr_not_queued_yet_joins_behind_every_entry(self):
        until = mq.derive_until(pr(queued=False, auto=True), queue(timeout_min=10, total=3), WALL0)
        # Three entries ahead, its own group, its own PR check: 5 x 10 min + grace.
        self.assertEqual(until, WALL0 + 5 * 600 + mq.DISPATCH_GRACE_S)
        self.assertIsNone(mq.derive_until(pr(), {"configuration": {}}, WALL0))

    def test_a_verdict_needs_no_queue_to_be_read(self):
        # Live, 2026-11-02: an already-merged PR read as exit 2 when the queue was
        # asked for its bound first.
        gh = FakeGh([pr(state="MERGED", queued=False, merge=M)], queue_answer={"configuration": {}})
        (code, line), _ = run_await(gh)
        self.assertEqual((code, line), (0, f"merged={M} pr=12"))
        (code, _), _ = run_await(FakeGh([pr(queued=False)], queue_answer={"configuration": {}}, runs=[]))
        self.assertEqual(code, 3)

    def test_an_unreadable_queue_config_stops_at_the_first_call(self):
        (code, line), _ = run_await(FakeGh([pr()], queue_answer={"configuration": {}}))
        self.assertEqual(code, 2)
        self.assertIn("checkResponseTimeout", line)

    def test_the_deadline_override_and_the_poll(self):
        (code, _), waited = run_await(FakeGh([pr()]), deadline_s=65)
        self.assertEqual((code, waited), (5, 65))
        self.assertEqual(mq.POLL_S, 30)

    def test_github_silent_for_a_whole_slice_is_not_a_verdict(self):
        (code, line), waited = run_await(FakeGh([None]), slice_s=60)
        self.assertEqual((code, waited), (2, 60))

    def test_await_requires_a_foreground_slice(self):
        for argv in (["await", "12", "--head", HEAD], ["await", "12", "--head", HEAD, "--slice-min", "10"]):
            with unittest.mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as ctx:
                mq.main(argv)
            self.assertEqual(ctx.exception.code, 2)


class DequeueTest(unittest.TestCase):
    def dequeue(self, gh):
        with unittest.mock.patch.object(mq, "gh_json", gh):
            return mq.dequeue("12", None)

    def test_dequeues_and_turns_auto_merge_off(self):
        gh = FakeGh([pr(auto=True)])
        code, line = self.dequeue(gh)
        self.assertEqual(code, 0)
        self.assertIn("removed from the merge queue, auto-merge turned off", line)
        mutations = [c[3] for c in gh.calls if c[:2] == ["api", "graphql"] and "mutation" in c[3]]
        self.assertEqual(len(mutations), 2)
        self.assertIn("dequeuePullRequest", mutations[0])
        self.assertIn("disablePullRequestAutoMerge", mutations[1])

    def test_nothing_queued(self):
        self.assertEqual(self.dequeue(FakeGh([pr(queued=False)]))[0], 1)

    def test_a_refused_mutation_is_reported(self):
        self.assertEqual(self.dequeue(FakeGh([pr()], mutation_ok=False))[0], 2)


class WaitMergeableQueueTest(unittest.TestCase):
    def run_wait(self, seq, deadline=180, queue=True):
        clock = Clock()
        it = iter(seq)
        with unittest.mock.patch.object(wm, "read", lambda *_a: next(it, seq[-1])):
            return wm.wait("12", HEAD, None, deadline, 5, clock=clock.now, sleep=clock.sleep,
                           queue=queue), clock.t

    @staticmethod
    def view(state):
        return {"state": "OPEN", "mergeStateStatus": state, "headRefOid": HEAD}

    def test_behind_is_fine_in_a_queue(self):
        (code, line), _ = self.run_wait([self.view("BEHIND")])
        self.assertEqual((code, line.split()[0]), (0, "merge_state=BEHIND"))
        (code, _), _ = self.run_wait([self.view("BEHIND")], queue=False)
        self.assertEqual(code, 3, "the direct path is unchanged")

    def test_blocked_waits_for_the_own_check_then_reports_at_the_bound(self):
        (code, _), waited = self.run_wait([self.view("BLOCKED"), self.view("BLOCKED"), self.view("CLEAN")])
        self.assertEqual((code, waited), (0, 10))
        (code, line), waited = self.run_wait([self.view("BLOCKED")], deadline=20)
        self.assertEqual((code, waited), (4, 20))
        self.assertIn("merge_state=BLOCKED", line)

    def test_dirty_is_still_a_conflict(self):
        (code, _), _ = self.run_wait([self.view("DIRTY")])
        self.assertEqual(code, 3)


class SkillContractTest(unittest.TestCase):
    """The landing skills choose the path from GitHub and land through the queue with
    the same merge command, waiting for the queue's verdict in foreground slices."""

    @staticmethod
    def skill(name):
        with open(os.path.join(os.path.dirname(SCRIPTS), "skills", name, "SKILL.md")) as fh:
            return " ".join(fh.read().split())

    def test_every_landing_skill_reads_the_path_and_never_guesses(self):
        for name in ("ship", "merge", "quickship"):
            text = self.skill(name)
            self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py mode", text, name)
            self.assertIn("never guess", text.lower(), name)
            self.assertIn("--head <", text, name)
            self.assertIn("--slice-min 9", text, name)
            self.assertIn("proves the merge result before main moves", text, name)

    def test_ship_and_merge_enqueue_with_the_reviewed_merge_command(self):
        for name in ("ship", "merge"):
            text = self.skill(name)
            queue = text[text.index("Through the merge queue" if name == "merge" else "## Land through the merge queue"):]
            self.assertIn("wait-mergeable.py <PR> --head <SHA> --queue", queue, name)
            self.assertIn("`gh pr merge <PR> --squash --match-head-commit <SHA>`", queue, name)
            self.assertIn("merge-queue.py await <PR> --head <SHA> --slice-min 9", queue, name)
            self.assertIn("merge-queue.py dequeue <PR>", queue, name)
            self.assertNotIn("gh pr merge <PR> --auto", queue, name)
        ship = self.skill("ship")
        self.assertIn("never a reason to integrate", ship)
        self.assertIn("under the same approval, which the removal did not spend", ship)

    def test_quickship_lands_through_its_own_branch_and_pr(self):
        text = self.skill("quickship")
        self.assertIn("release-actions: push-main | merge", text)
        self.assertIn("git push origin HEAD:refs/heads/quickship/<S>", text)
        self.assertIn("gh pr create --base main --head quickship/<S>", text)
        self.assertIn("gh pr merge <PR> --squash --match-head-commit <C>", text)
        self.assertIn("`<P>` is `<M>`", text)
        self.assertIn("Only main, in the primary checkout.", text)


if __name__ == "__main__":
    unittest.main()
