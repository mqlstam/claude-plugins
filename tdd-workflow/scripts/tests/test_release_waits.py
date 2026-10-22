"""Tests for wait-mergeable.py and release-run.py (the two bounded waits of the release
skills)."""
import datetime
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wm = load("wait_mergeable", "wait-mergeable.py")
rr = load("release_run", "release-run.py")


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def answers(seq):
    it = iter(seq)
    return lambda *_a, **_k: next(it, seq[-1])


HEAD = "abc1234def"


def view(state, head=HEAD, pr_state="OPEN"):
    return {"state": pr_state, "mergeStateStatus": state, "headRefOid": head}


class WaitMergeableTest(unittest.TestCase):
    def run_wait(self, seq, deadline=180, poll=5):
        clock = Clock()
        old = wm.read
        wm.read = answers(seq)
        try:
            return wm.wait("12", HEAD, None, deadline, poll, clock=clock.now, sleep=clock.sleep), clock.t
        finally:
            wm.read = old

    def test_waits_through_unknown_until_clean(self):
        (code, line), waited = self.run_wait([view("UNKNOWN"), view("UNKNOWN"), view("CLEAN")])
        self.assertEqual(code, 0)
        self.assertIn("merge_state=CLEAN", line)
        self.assertEqual(waited, 10)

    def test_a_state_of_the_previous_head_does_not_count(self):
        # Right after a push GitHub can still describe the old head: DIRTY there is stale.
        (code, line), _ = self.run_wait([view("DIRTY", head="0ld0ld"), view("CLEAN")])
        self.assertEqual((code, line.split()[0]), (0, "merge_state=CLEAN"))

    def test_settled_states_map_to_their_exit(self):
        for state, code in (("HAS_HOOKS", 0), ("DIRTY", 3), ("BEHIND", 3), ("BLOCKED", 4)):
            (got, _), _ = self.run_wait([view(state)])
            self.assertEqual(got, code, state)

    def test_unstable_waits_for_clean_then_reports_at_the_bound(self):
        (code, _), _ = self.run_wait([view("UNSTABLE"), view("CLEAN")])
        self.assertEqual(code, 0)
        (code, line), waited = self.run_wait([view("UNSTABLE")], deadline=20)
        self.assertEqual((code, waited), (6, 20))
        self.assertIn("UNSTABLE", line)

    def test_the_bound_is_named(self):
        (code, line), waited = self.run_wait([view("UNKNOWN")], deadline=30)
        self.assertEqual((code, waited), (5, 30))
        self.assertIn("merge_state_wait_deadline", line)
        (code, _), _ = self.run_wait([None], deadline=10)
        self.assertEqual(code, 5)

    def test_a_merged_pr_stops_the_wait(self):
        (code, line), _ = self.run_wait([view("UNKNOWN", pr_state="MERGED")])
        self.assertEqual(code, 7)
        self.assertIn("MERGED", line)

    def test_the_default_bound_is_the_derived_one(self):
        self.assertEqual((wm.DEADLINE_S, wm.POLL_S), (180, 5))


class ReleaseRunTest(unittest.TestCase):
    def with_gh(self, seq, fn, *args):
        clock = Clock()
        old = rr.gh_json
        rr.gh_json = answers(seq)
        try:
            return fn(*args, clock=clock.now, sleep=clock.sleep), clock.t
        finally:
            rr.gh_json = old

    def test_find_waits_for_registration_and_takes_the_newest(self):
        runs = [{"databaseId": 1, "url": "u1", "createdAt": "2026-10-12T10:00:00Z"},
                {"databaseId": 2, "url": "u2", "createdAt": "2026-10-12T10:05:00Z"}]
        (code, line), waited = self.with_gh([[], [], runs], rr.find, "deploy.yml", "a" * 40, None, 60, 3)
        self.assertEqual((code, line, waited), (0, "run_id=2 url=u2", 6))

    def test_find_names_its_bound(self):
        (code, line), waited = self.with_gh([[]], rr.find, "deploy.yml", "a" * 40, None, 9, 3)
        self.assertEqual((code, waited), (5, 9))
        self.assertIn("release_run_find_deadline", line)

    def test_the_verdict_is_the_conclusion_after_completed(self):
        seq = [{"status": "in_progress", "conclusion": "", "url": "u"},
               {"status": "completed", "conclusion": "success", "url": "u"}]
        (code, line), _ = self.with_gh(seq, rr.await_run, "9", None, 600, 30)
        self.assertEqual((code, line), (0, "conclusion=success run_id=9 url=u"))
        seq = [{"status": "completed", "conclusion": "failure", "url": "u"}]
        (code, line), _ = self.with_gh(seq, rr.await_run, "9", None, 600, 30)
        self.assertEqual(code, 1)
        self.assertIn("conclusion=failure", line)

    def test_an_in_flight_conclusion_is_not_a_verdict(self):
        seq = [{"status": "in_progress", "conclusion": "success", "url": "u"}]
        (code, line), waited = self.with_gh(seq, rr.await_run, "9", None, 90, 30)
        self.assertEqual((code, waited), (5, 90))
        self.assertIn("release_run_await_deadline NOT CONCLUDED run_id=9 status=in_progress", line)

    def test_await_requires_a_derived_deadline(self):
        with self.assertRaises(SystemExit):
            rr.main(["await", "9"])

    def test_every_bound_is_a_finite_positive_number(self):
        # Codex review round 4: `--deadline-min nan` made min(end, slice_end) NaN, so
        # the pause took the whole poll interval past the slice. Every number that
        # bounds a wait is read as finite and above 0 at the parser, the one boundary.
        import contextlib
        import io

        def waited(*_a, **_k):
            raise AssertionError("waited on a bound that is not a finite positive number")

        base = ["await", "9", "--deadline-min", "190", "--slice-min", "9"]
        for flag in ("--deadline-min", "--slice-min", "--poll-s"):
            for bad in ("nan", "inf", "-inf", "-1"):
                args = [*base, flag, bad] if flag == "--poll-s" else \
                    [w if base[i - 1] != flag else bad for i, w in enumerate(base)]
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop, \
                        unittest.mock.patch.object(rr, "await_run", side_effect=waited):
                    rr.main(args)
                self.assertEqual(stop.exception.code, 2, args)
        for flag in ("--deadline-s", "--poll-s"):
            for bad in ("nan", "inf", "0"):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop, \
                        unittest.mock.patch.object(rr, "find", side_effect=waited):
                    rr.main(["find", "deploy.yml", "abc", flag, bad])
                self.assertEqual(stop.exception.code, 2, (flag, bad))

    def test_await_requires_a_foreground_slice(self):
        # Owner, 2026-10-21: the slice is required by the script itself, so the
        # foreground requirement holds however the command is spelled. Mutation:
        # an optional --slice-min waits the whole deadline in one call.
        import contextlib
        import io

        def waited(*_a, **_k):
            raise AssertionError("waited without a foreground slice")

        for args in (["await", "9", "--deadline-min", "190"],
                     ["await", "9", "--deadline-min", "190", "--slice-min", "0"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop, \
                    unittest.mock.patch.object(rr, "await_run", side_effect=waited):
                rr.main(args)
            self.assertEqual(stop.exception.code, 2, args)
            self.assertIn("--slice-min", err.getvalue(), args)
            self.assertIn("foreground", err.getvalue(), args)

    def test_a_slice_is_at_most_one_foreground_call(self):
        # The hard contract (owner, 2026-10-22): the Bash guard is advisory, so the
        # script bounds the loss itself. A detached await loses at most one slice of
        # verdict, and a slice fits one foreground call. Mutation: an uncapped
        # --slice-min lets a detached await hold the verdict for hours.
        import contextlib
        import io
        for slice_min in ("10", "600"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop, \
                    unittest.mock.patch.object(rr, "await_run", side_effect=AssertionError("waited")):
                rr.main(["await", "9", "--deadline-min", "190", "--slice-min", slice_min])
            self.assertEqual(stop.exception.code, 2, slice_min)
            self.assertIn(f"at most {rr.FOREGROUND_SLICE_MIN}", err.getvalue(), slice_min)
        with unittest.mock.patch.object(rr, "await_run", return_value=(6, "x")) as waited, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rr.main(["await", "9", "--deadline-min", "190", "--slice-min", "9"]), 6)
        self.assertEqual(waited.call_args.args[4], 9 * 60)

    def test_a_failed_verdict_carries_its_reason(self):
        seq = [{"status": "completed", "conclusion": "failure", "url": "u"}]
        old = rr.gh_json
        rr.gh_json = answers(seq)
        try:
            code, out = rr.await_run("9", None, 600, 30, explain=lambda *_: (1, ["reason=billing x"]),
                                     clock=Clock().now, sleep=lambda _s: None)
        finally:
            rr.gh_json = old
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), ["conclusion=failure run_id=9 url=u", "reason=billing x"])

    def test_a_slice_ends_with_exit_6_and_the_bound_counts_from_the_run_start(self):
        # The run started 100 min before this call: of a 190 min bound, 90 remain;
        # a 9 min slice ends with 81 left and asks to be run again.
        now = 1_800_000_000.0
        started = "2027-01-15T06:20:00Z"  # 100 min before `now`
        self.assertEqual(rr._epoch(started), now - 6000)
        seq = [{"status": "in_progress", "conclusion": "", "url": "u", "startedAt": started}]
        clock = Clock()
        old = rr.gh_json
        rr.gh_json = answers(seq)
        try:
            code, line = rr.await_run("9", None, 190 * 60, 30, slice_s=9 * 60, clock=clock.now,
                                      sleep=clock.sleep, wall=lambda: now)
            self.assertEqual((code, clock.t), (6, 540))
            self.assertIn("release_run_still_running", line)
            self.assertIn("81min left of the run's 190min bound", line)
            # Started 200 min ago: past its bound on the first read, whatever the slice.
            clock = Clock()
            code, line = rr.await_run("9", None, 190 * 60, 30, slice_s=9 * 60, clock=clock.now,
                                      sleep=clock.sleep, wall=lambda: now + 6000)
            self.assertEqual((code, clock.t), (5, 0))
            self.assertIn("release_run_await_deadline", line)
        finally:
            rr.gh_json = old

    def test_the_slice_fits_one_foreground_bash_call(self):
        # Claude Code's Bash tool stops a foreground command at 600000 ms at most.
        self.assertLess(rr.FOREGROUND_SLICE_MIN * 60 + rr.GH_CALL_TIMEOUT_S, 600)


class SlowGh:
    """A stand-in for `gh_json` on a fake clock: each call takes `takes(args, timeout)`
    seconds (a hung API takes its whole timeout), then answers `answer(args)`, or None
    when it timed out. Records every call's arguments, start and timeout."""

    def __init__(self, clock, answer, takes=lambda _args, timeout: timeout):
        self.clock, self.answer, self.takes = clock, answer, takes
        self.calls = []

    def __call__(self, args, timeout=rr.GH_CALL_TIMEOUT_S):
        self.calls.append((args, self.clock.t, timeout))
        if timeout <= 0:
            return None
        spent = self.takes(args, timeout)
        self.clock.t += min(spent, timeout)
        return None if spent >= timeout else self.answer(args)


class BoundedWaitTest(unittest.TestCase):
    """Every invocation returns by its stop (the run's deadline or the slice's end,
    whichever is first) plus one `gh` call, however slowly GitHub answers."""

    def run_await(self, gh, clock, deadline_s, slice_s, wall=lambda: 1_800_000_000.0, poll_s=30):
        old = rr.gh_json
        rr.gh_json = gh
        try:
            return rr.await_run("9", None, deadline_s, poll_s, slice_s=slice_s, clock=clock.now,
                                sleep=clock.sleep, wall=wall)
        finally:
            rr.gh_json = old

    def test_a_hung_api_keeps_the_slice_and_the_deadline(self):
        # Mutation: an unclamped `sleep(poll_s)` returns up to 55 s past the stop here.
        for stop in range(480, 600):
            for deadline_s, slice_s in ((stop, 9 * 60), (190 * 60, stop)):
                clock = Clock()
                gh = SlowGh(clock, answer=lambda _a: None)
                code, _ = self.run_await(gh, clock, deadline_s, slice_s)
                bound = min(deadline_s, slice_s)
                self.assertIn(code, (5, 6))
                self.assertLessEqual(clock.t, bound + rr.GH_CALL_TIMEOUT_S, (deadline_s, slice_s))
                self.assertTrue(all(0 < t <= rr.GH_CALL_TIMEOUT_S for _a, _s, t in gh.calls), gh.calls)
                self.assertTrue(all(s <= bound for _a, s, _t in gh.calls), "no call starts past the stop")

    def test_a_failing_api_keeps_the_deadline(self):
        clock = Clock()
        gh = SlowGh(clock, answer=lambda _a: None, takes=lambda _a, _t: 0.5)
        code, line = self.run_await(gh, clock, 515, None)
        self.assertEqual((code, clock.t), (5, 515.5))
        self.assertIn("NOT CONCLUDED", line)

    def test_the_reason_reads_share_the_slice(self):
        # The billing run concludes near the end of a slice and every annotation read
        # hangs: the verdict still comes back inside the slice plus one call, with the
        # unread jobs named. Mutation: reading the reason without the call's stop
        # (each read its own 30 s) returns at 750 s, past the Bash tool's 600.
        billing = {**fixture("run-39493655874-billing.view.json"), "status": "completed"}
        clock = Clock()

        def answer(args):
            if args[:2] == ["run", "view"] and "jobs" in args[4]:
                return billing
            done = clock.t >= 520
            return {"status": "completed" if done else "in_progress",
                    "conclusion": "failure" if done else "", "url": "u"}

        gh = SlowGh(clock, answer, takes=lambda args, timeout: timeout if args[0] == "api" else 0)
        code, out = self.run_await(gh, clock, 190 * 60, 9 * 60)
        self.assertEqual(code, 1)
        self.assertLessEqual(clock.t, 9 * 60 + rr.GH_CALL_TIMEOUT_S)
        self.assertEqual(out.splitlines()[0], "conclusion=failure run_id=9 url=u")
        self.assertIn("reason=annotations-unread", out)
        self.assertIn("ran out of time", out)

    def test_the_reason_reads_keep_the_runs_earlier_deadline(self):
        # Review finding (Codex, 2026-10-21): 10 s left of the run's bound, a 9 min
        # slice, a failed run and seven annotation reads that hang. The reads share the
        # earlier stop, the run's, so the call returns by it plus one call, naming the
        # jobs it could not read. Mutation: the slice's stop returns after 210 s.
        now = 1_800_000_000.0
        billing = {**fixture("run-39493655874-billing.view.json"), "status": "completed"}
        started = datetime.datetime.fromtimestamp(now - (190 * 60 - 10), datetime.timezone.utc).isoformat()

        def answer(args):
            if "jobs" in args[4]:
                return billing
            return {"status": "completed", "conclusion": "failure", "url": "u", "startedAt": started}

        clock = Clock()
        gh = SlowGh(clock, answer, takes=lambda args, timeout: timeout if args[0] == "api" else 0)
        code, out = self.run_await(gh, clock, 190 * 60, 9 * 60, wall=lambda: now)
        self.assertEqual(code, 1)
        self.assertLessEqual(clock.t, 10 + rr.GH_CALL_TIMEOUT_S)
        self.assertEqual(out.splitlines()[0], "conclusion=failure run_id=9 url=u")
        self.assertIn("reason=annotations-unread", out)

    def test_a_rerun_attempt_moves_the_bound_to_its_own_start(self):
        # Review finding (Codex, 2026-10-22): the first attempt's bound ends 10 s into
        # the call; GitHub starts a re-run attempt at second 5, which resets
        # `startedAt`. The bound is the new attempt's, as the next slice would read it:
        # 600 s from second 5. Mutation: anchoring once exits 5 at second 10.
        now = 1_800_000_000.0

        def stamp(epoch):
            return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()

        def answer(_args):
            # 605 s at one read per 5 s is 122 reads; more is a wait that stopped moving
            # (a stop pinned to the first attempt's deadline sleeps 0 s for ever).
            self.assertLess(len(gh.calls), 200, "the wait stopped advancing")
            began = now - 590 if clock.t < 5 else now + 5
            return {"status": "in_progress", "conclusion": "", "url": "u", "startedAt": stamp(began)}

        for slice_s, expected in ((9 * 60, (6, 540)), (None, (5, 605))):
            clock = Clock()
            gh = SlowGh(clock, answer, takes=lambda _a, _t: 0)
            code, line = self.run_await(gh, clock, 600, slice_s, wall=lambda: now + clock.t, poll_s=5)
            self.assertEqual((code, clock.t), expected, line)
        self.assertIn("release_run_await_deadline", line)

    def test_a_run_past_its_own_deadline_reports_its_verdict_and_where_the_reason_is(self):
        # The bound's one call past the stop was the read that found the verdict; the
        # reason is named as unread, with the command that reads it.
        now = 1_800_000_000.0
        started = "2027-01-15T01:20:00Z"  # 400 min before `now`, past a 305 min bound
        clock = Clock()
        gh = SlowGh(clock, lambda _a: {"status": "completed", "conclusion": "failure", "url": "u",
                                       "startedAt": started}, takes=lambda _a, _t: 0.5)
        code, out = self.run_await(gh, clock, 305 * 60, 9 * 60, wall=lambda: now)
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], "conclusion=failure run_id=9 url=u")
        self.assertIn("run `release-run.py reason 9`", out)
        self.assertEqual(clock.t, 0.5, "one read, the one that found the verdict")
        self.assertTrue(all(t <= 0 for _a, _s, t in gh.calls[1:]), "no read past the bound")

    def test_an_empty_start_time_counts_from_the_creation(self):
        # gh prints a null run_started_at as Go's zero time. Mutation: reading it as a
        # time puts the run's start in year 1 and declares it past its bound at once.
        now = 1_800_000_000.0
        created = "2027-01-15T07:50:00Z"  # 10 min before `now`
        self.assertIsNone(rr._epoch("0001-01-01T00:00:00Z"))
        clock = Clock()
        gh = SlowGh(clock, lambda _a: {"status": "queued", "conclusion": "", "url": "u",
                                       "createdAt": created, "startedAt": "0001-01-01T00:00:00Z"},
                    takes=lambda _a, _t: 0)
        code, line = self.run_await(gh, clock, 190 * 60, 9 * 60, wall=lambda: now)
        self.assertEqual(code, 6)
        self.assertIn("171min left of the run's 190min bound", line)

    def test_the_bound_is_what_is_left_when_the_run_is_read(self):
        # The first read takes 20 s. The run started 100 min before this call, so its
        # 190 min bound ends 90 min after the call began, not 20 s earlier. Mutation:
        # anchoring on the call's start instead of the read's instant ends at 5380 s.
        base = 1_800_000_000.0
        clock = Clock()
        started = datetime.datetime.fromtimestamp(base - 6000, datetime.timezone.utc).isoformat()
        gh = SlowGh(clock, lambda _a: {"status": "in_progress", "conclusion": "", "url": "u",
                                       "startedAt": started},
                    takes=lambda _a, _t: 20 if not gh.calls[1:] else 0)
        code, _ = self.run_await(gh, clock, 190 * 60, None, wall=lambda: base + clock.t)
        self.assertEqual((code, clock.t), (5, 5400))

    def test_find_keeps_its_deadline_under_a_hung_api(self):
        # Mutation: an unclamped `sleep(poll_s)` or an underived timeout ends past 60 + 30.
        for deadline_s in range(40, 70):
            clock = Clock()
            gh = SlowGh(clock, answer=lambda _a: None, takes=lambda _a, t: min(t, 7))
            old = rr.gh_json
            rr.gh_json = gh
            try:
                code, _ = rr.find("deploy.yml", "a" * 40, None, deadline_s, 3,
                                  clock=clock.now, sleep=clock.sleep)
            finally:
                rr.gh_json = old
            self.assertEqual(code, 5)
            self.assertLessEqual(clock.t, deadline_s + rr.GH_CALL_TIMEOUT_S)
            self.assertTrue(all(s <= deadline_s for _a, s, _t in gh.calls))

    def test_reason_on_its_own_fits_one_foreground_call(self):
        # Mutation: an annotation read with a fixed 30 s timeout takes 8 x 30 s here.
        billing = {**fixture("run-39493655874-billing.view.json"), "status": "completed"}
        clock = Clock()
        gh = SlowGh(clock, lambda args: billing if args[0] == "run" else None,
                    takes=lambda args, timeout: timeout if args[0] == "api" else 0)
        old = rr.gh_json
        rr.gh_json = gh
        try:
            code, lines = rr.reason("9", None, stop=45.0, clock=clock.now)
        finally:
            rr.gh_json = old
        self.assertEqual(code, 1)
        self.assertLessEqual(clock.t, 45 + rr.GH_CALL_TIMEOUT_S)
        self.assertIn("ran out of time", lines[-1])


class GhCallTimeoutTest(unittest.TestCase):
    """The timeout reaches the real `gh` process: a hung `gh` is killed at it."""

    def test_a_hung_gh_is_killed_at_its_timeout(self):
        # Mutation: dropping `timeout=` from subprocess.run waits the fake's full 20 s.
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        directory = holder.name
        gh = os.path.join(directory, "gh")
        with open(gh, "w") as fh:
            fh.write(f"#!{sys.executable}\nimport time\ntime.sleep(20)\nprint('[]')\n")
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = directory + os.pathsep + old
        try:
            began = time.monotonic()
            self.assertIsNone(rr.gh_json(["run", "list"], timeout=1))
            self.assertLess(time.monotonic() - began, 10)
        finally:
            os.environ["PATH"] = old

    def test_no_time_left_starts_no_gh(self):
        # Mutation: without the guard, `gh` is started with a timeout of zero or less.
        with unittest.mock.patch.object(rr.subprocess, "run", side_effect=AssertionError("gh started")):
            for timeout in (0, -12.5):
                self.assertIsNone(rr.gh_json(["run", "list"], timeout=timeout))


FIXTURES = os.path.join(HERE, "fixtures", "release-run")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


class ReleaseRunReasonTest(unittest.TestCase):
    """Recorded from EndoxiaBV/Endoxia: run 39493655874 (2026-10-20, every job refused
    for billing) and run 39389020404 (2026-10-20, the integration suite failed)."""

    BILLING = ("reason=billing GitHub Actions refused to start jobs: billing or spending limit; "
               "fix under Settings → Billing & plans")

    def annotations(self, by_job):
        return lambda job: by_job.get(job.get("databaseId"))

    def test_a_billing_refusal_is_one_owner_action_line(self):
        view = fixture("run-39493655874-billing.view.json")
        refused = fixture("annotations-118873000702-billing.json")
        lines = rr.classify(view, lambda _job: refused)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(self.BILLING), lines[0])
        self.assertIn("owner action; 7 of 7 failed jobs were never started", lines[0])

    def test_a_test_failure_names_the_job_step_and_annotations_not_billing(self):
        view = fixture("run-39389020404-test-failure.view.json")
        notes = fixture("annotations-118515764377-test-failure.json")
        lines = rr.classify(view, self.annotations({118515764377: notes}))
        self.assertFalse([l for l in lines if "billing" in l], lines)
        self.assertEqual(lines[0], "reason=job-failure 1 failed job(s):")
        self.assertEqual(lines[1], "job failed: Integration suite (real Postgres) (failure), step: Integration suite")
        self.assertIn("  annotation: BudgetDeniedError: LLM token reservation denied — weekly allowance "
                      "exhausted for user 63f52314-8aa5-497c-99d0-3312623dab04", lines)
        # A notice (the runner-image migration) is not a reason.
        self.assertFalse([l for l in lines if "ubuntu-latest label" in l], lines)

    def test_the_other_wordings_of_a_billing_refusal(self):
        for message in ("The job was not started because your account is locked due to a billing issue.",
                        "The job was not started because recent account payments have failed or your "
                        "spending limit needs to be increased. Please check the 'Billing & plans' section"):
            self.assertTrue(rr.is_billing_refusal(message), message)
        self.assertFalse(rr.is_billing_refusal("Process completed with exit code 1."))
        # A test that merely talks about a spending cap is not GitHub refusing the job.
        self.assertFalse(rr.is_billing_refusal("BudgetDeniedError: SPEND_CAP_EXCEEDED spending limit"))
        # The reason word must come after the refusal, as GitHub writes it.
        self.assertFalse(rr.is_billing_refusal("billing: the job was not started because of a matrix error"))
        # A character that lowers to two (`İ`) before the refusal moves no offset.
        self.assertTrue(rr.is_billing_refusal("İ: job was not started because billing"))

    def test_an_answer_above_the_cap_counts_as_failed(self):
        # Codex review round 6: parsing an unbounded `gh` answer ran past the bound.
        done = subprocess.CompletedProcess(["gh"], 0, stdout=b"[" + b" " * rr.GH_MAX_RESPONSE_BYTES + b"]",
                                           stderr=b"")
        with unittest.mock.patch.object(rr.subprocess, "run", return_value=done):
            self.assertIsNone(rr.gh_json(["api", "x"]))
        # Round 7: the cap counts bytes, not decoded characters (4 bytes each here).
        done.stdout = ('["' + "😀" * (rr.GH_MAX_RESPONSE_BYTES // 4) + '"]').encode()
        with unittest.mock.patch.object(rr.subprocess, "run", return_value=done):
            self.assertIsNone(rr.gh_json(["api", "x"]))
        done.stdout = '["é"]'.encode()
        with unittest.mock.patch.object(rr.subprocess, "run", return_value=done):
            self.assertEqual(rr.gh_json(["api", "x"]), ["é"])

    def test_no_call_starts_past_the_stop(self):
        # Round 7: a call that started at 539 of a 540 stop and returned at 545 let
        # the next call start at 545 with 25 s. At the stop itself the last status
        # read still runs.
        self.assertEqual(rr._call_timeout(540, 545), 0)
        self.assertEqual(rr._call_timeout(540, 540.001), 0)
        self.assertEqual(rr._call_timeout(540, 540), rr.GH_CALL_TIMEOUT_S)
        self.assertEqual(rr._call_timeout(540, 539), rr.GH_CALL_TIMEOUT_S)
        self.assertEqual(rr._call_timeout(540, 100), rr.GH_CALL_TIMEOUT_S)

    def test_reading_a_huge_annotation_is_linear(self):
        # Codex review round 5: `job was not started because .*(…)` backtracked
        # quadratically, so 50 annotations of the phrase repeated 1,000 times took
        # 99.9 s inside the await's bound. A linear read keeps classify within
        # milliseconds whatever GitHub returns.
        import time as real_time
        message = "job was not started because " * 1000
        view = {"conclusion": "failure", "url": "-",
                "jobs": [{"name": "build", "conclusion": "failure", "steps": []}]}
        notes = [{"annotation_level": "failure", "message": message}] * 50
        began = real_time.perf_counter()
        rr.classify(view, lambda _job: notes)
        self.assertLess(real_time.perf_counter() - began, 2.0)

    def test_a_run_without_jobs_and_unreadable_annotations_are_named(self):
        lines = rr.classify({"conclusion": "startup_failure", "jobs": []}, lambda _j: [])
        self.assertTrue(lines[0].startswith("reason=no-jobs conclusion=startup_failure"), lines)
        view = fixture("run-39389020404-test-failure.view.json")
        lines = rr.classify(view, lambda _j: None)
        self.assertEqual(lines, ["reason=annotations-unread for Integration suite (real Postgres): "
                                 "`gh api` failed or ran out of time; read `gh run view <id> --log-failed`"])

    def test_reason_reads_each_failed_job_once_from_the_runs_own_repository(self):
        view = {**fixture("run-39493655874-billing.view.json"), "status": "completed"}
        refused = fixture("annotations-118873000702-billing.json")
        calls = []

        def fake(args, timeout):
            self.assertTrue(0 < timeout <= rr.GH_CALL_TIMEOUT_S, timeout)
            calls.append(args)
            return view if args[:2] == ["run", "view"] else refused

        old = rr.gh_json
        rr.gh_json = fake
        try:
            code, lines = rr.reason("39493655874", "EndoxiaBV/Endoxia")
        finally:
            rr.gh_json = old
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith(self.BILLING))
        api = [c for c in calls if c[0] == "api"]
        self.assertEqual(len(api), 7, "one read per failed job")
        self.assertEqual(api[0], ["api", "repos/EndoxiaBV/Endoxia/check-runs/118873000702/annotations?per_page=100"])


class FakeGhEndToEndTest(unittest.TestCase):
    """The CLI wiring against a stand-in `gh` on PATH: the arguments each script sends."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.dir = holder.name
        self.log = os.path.join(self.dir, "argv.jsonl")
        gh = os.path.join(self.dir, "gh")
        with open(gh, "w") as fh:
            fh.write(f"#!{sys.executable}\nimport json, sys\n"
                     f"open({self.log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                     "print(json.dumps({'state': 'OPEN', 'mergeStateStatus': 'CLEAN', "
                     "'headRefOid': 'abc1234def', 'status': 'completed', 'conclusion': 'success', "
                     "'url': 'u'}) if sys.argv[1] in ('pr', 'run') and sys.argv[2] == 'view' else "
                     "json.dumps([{'databaseId': 7, 'url': 'u7', 'createdAt': 'x'}]))\n")
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.env = {**os.environ, "PATH": self.dir + os.pathsep + os.environ.get("PATH", "")}

    def run_script(self, *args):
        return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True,
                              env=self.env, timeout=30)

    def argv(self):
        with open(self.log) as fh:
            return [json.loads(l) for l in fh]

    def test_wait_mergeable_cli(self):
        out = self.run_script(os.path.join(SCRIPTS, "wait-mergeable.py"), "12", "--head", "abc1234",
                              "--repo", "o/r")
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "merge_state=CLEAN head=abc1234def"))
        self.assertEqual(self.argv()[0], ["pr", "view", "12", "--json", "state,mergeStateStatus,headRefOid",
                                          "--repo", "o/r"])

    def test_release_run_cli(self):
        out = self.run_script(os.path.join(SCRIPTS, "release-run.py"), "find", "deploy.yml", "f" * 40)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "run_id=7 url=u7"))
        out = self.run_script(os.path.join(SCRIPTS, "release-run.py"), "await", "7", "--deadline-min", "1",
                              "--slice-min", "1")
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "conclusion=success run_id=7 url=u"))
        calls = self.argv()
        self.assertEqual(calls[0][:6], ["run", "list", "--workflow", "deploy.yml", "--commit", "f" * 40])
        self.assertEqual(calls[1][:3], ["run", "view", "7"])


if __name__ == "__main__":
    unittest.main()
