"""Tests for wait-mergeable.py and release-run.py (the two bounded waits the release
skills hand to the background)."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

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
            self.assertTrue(rr.BILLING_REFUSAL.search(message), message)
        self.assertFalse(rr.BILLING_REFUSAL.search("Process completed with exit code 1."))
        # A test that merely talks about a spending cap is not GitHub refusing the job.
        self.assertFalse(rr.BILLING_REFUSAL.search("BudgetDeniedError: SPEND_CAP_EXCEEDED spending limit"))

    def test_a_run_without_jobs_and_unreadable_annotations_are_named(self):
        lines = rr.classify({"conclusion": "startup_failure", "jobs": []}, lambda _j: [])
        self.assertTrue(lines[0].startswith("reason=no-jobs conclusion=startup_failure"), lines)
        view = fixture("run-39389020404-test-failure.view.json")
        lines = rr.classify(view, lambda _j: None)
        self.assertEqual(lines, ["reason=annotations-unread for Integration suite (real Postgres): "
                                 "`gh api` failed; read `gh run view <id> --log-failed`"])

    def test_reason_reads_each_failed_job_once_from_the_runs_own_repository(self):
        view = {**fixture("run-39493655874-billing.view.json"), "status": "completed"}
        refused = fixture("annotations-118873000702-billing.json")
        calls = []

        def fake(args):
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
        self.dir = tempfile.mkdtemp()
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
        out = self.run_script(os.path.join(SCRIPTS, "release-run.py"), "await", "7", "--deadline-min", "1")
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "conclusion=success run_id=7 url=u"))
        calls = self.argv()
        self.assertEqual(calls[0][:6], ["run", "list", "--workflow", "deploy.yml", "--commit", "f" * 40])
        self.assertEqual(calls[1][:3], ["run", "view", "7"])


if __name__ == "__main__":
    unittest.main()
