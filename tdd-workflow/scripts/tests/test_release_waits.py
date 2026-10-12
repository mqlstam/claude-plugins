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
        self.assertIn("release_run_await_deadline", line)

    def test_await_requires_a_derived_deadline(self):
        with self.assertRaises(SystemExit):
            rr.main(["await", "9"])


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
