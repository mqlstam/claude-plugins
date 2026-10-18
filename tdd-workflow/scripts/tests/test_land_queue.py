"""Tests for land_queue.py against real git repositories. Run: python3 -m unittest discover -s scripts/tests"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lq = load("land_queue", "land_queue.py")
gate = load("gate_for_land_queue", "release-approval-gate.py")

# A git that reads none of the machine's own configuration: no global hooks, signing
# or templates may change what these repositories are.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

QUEUE_SCRIPTS = {"ship:gate": "node scripts/ship-gate.mjs", "ship:land": "node scripts/ship-land.mjs"}


def git(cwd, *args):
    out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, env=GIT_ENV)
    if out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {cwd}: {out.stderr}")
    return out.stdout.strip()


class Repos:
    """An `origin` bare repository and a clone of it, in a temporary directory."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="land-queue-")
        self.origin = os.path.join(self.root, "origin.git")
        self.clone = os.path.join(self.root, "clone")
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", self.origin], check=True, env=GIT_ENV)
        subprocess.run(["git", "clone", "--quiet", self.origin, self.clone], check=True, env=GIT_ENV,
                       capture_output=True)
        git(self.clone, "symbolic-ref", "HEAD", "refs/heads/main")

    def commit(self, scripts=None, package=True, message="c"):
        path = os.path.join(self.clone, "package.json")
        if package:
            with open(path, "w") as fh:
                json.dump({"name": "x", "scripts": scripts or {}}, fh)
        elif os.path.exists(path):
            os.unlink(path)
        with open(os.path.join(self.clone, "README"), "a") as fh:
            fh.write(message + "\n")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "--quiet", "-m", message)

    def push_main(self):
        git(self.clone, "push", "--quiet", "origin", "HEAD:main")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class LandQueueTest(unittest.TestCase):
    def setUp(self):
        self.repos = Repos()
        self.addCleanup(self.repos.close)

    def test_main_without_the_land_script_has_no_queue(self):
        self.repos.commit({"lint": "eslint ."})
        self.repos.push_main()
        self.assertIs(lq.on_main(self.repos.clone), False)
        self.assertEqual(lq.verdict(self.repos.clone, fetch=True), "none")

    def test_main_with_the_land_script_has_a_queue(self):
        self.repos.commit(QUEUE_SCRIPTS)
        self.repos.push_main()
        self.assertIs(lq.on_main(self.repos.clone), True)
        self.assertEqual(lq.verdict(self.repos.clone, fetch=True), "queue")

    def test_a_branch_cut_before_the_queue_landed_is_told_to_merge_main_first(self):
        self.repos.commit({"lint": "eslint ."})
        self.repos.push_main()
        git(self.repos.clone, "checkout", "--quiet", "-b", "old-branch")
        # main gains the queue from another clone; this branch never merged it.
        other = os.path.join(self.repos.root, "other")
        subprocess.run(["git", "clone", "--quiet", self.repos.origin, other], check=True, env=GIT_ENV,
                       capture_output=True)
        with open(os.path.join(other, "package.json"), "w") as fh:
            json.dump({"name": "x", "scripts": QUEUE_SCRIPTS}, fh)
        git(other, "commit", "--quiet", "-am", "the queue lands")
        git(other, "push", "--quiet", "origin", "HEAD:main")
        # Before a fetch the local ref still says no queue: that is why the skills fetch.
        self.assertEqual(lq.verdict(self.repos.clone, fetch=False), "none")
        self.assertEqual(lq.verdict(self.repos.clone, fetch=True), "merge-main-first")
        self.assertIs(lq.on_main(self.repos.clone), True, "the hook reads the fetched ref")
        git(self.repos.clone, "merge", "--quiet", "--no-edit", "origin/main")
        self.assertEqual(lq.verdict(self.repos.clone, fetch=True), "queue")

    def test_the_branch_that_brings_the_queue_lands_the_way_main_does(self):
        self.repos.commit({"lint": "eslint ."})
        self.repos.push_main()
        git(self.repos.clone, "checkout", "--quiet", "-b", "slice-806")
        self.repos.commit(QUEUE_SCRIPTS)
        self.assertEqual(lq.verdict(self.repos.clone, fetch=True), "none")

    def test_main_without_a_package_json_or_without_a_main_has_no_queue(self):
        self.repos.commit(package=False)
        self.assertIs(lq.on_main(self.repos.clone), False, "nothing pushed: no origin/main")
        self.repos.push_main()
        self.assertIs(lq.on_main(self.repos.clone), False, "no package.json on main")

    def test_unreadable_is_unknown(self):
        self.assertIsNone(lq.on_main(os.path.join(self.repos.root, "gone")))
        self.assertIsNone(lq.on_main(self.repos.root), "a directory that is no checkout")
        with open(os.path.join(self.repos.clone, "package.json"), "w") as fh:
            fh.write("{ not json")
        git(self.repos.clone, "add", "-A")
        git(self.repos.clone, "commit", "--quiet", "-m", "broken")
        self.repos.push_main()
        self.assertIsNone(lq.on_main(self.repos.clone))
        self.assertTrue(lq.verdict(self.repos.clone, fetch=True).startswith("unknown"))

    def test_a_failed_fetch_decides_nothing(self):
        self.repos.commit({"lint": "eslint ."})
        self.repos.push_main()
        git(self.repos.clone, "remote", "set-url", "origin", os.path.join(self.repos.root, "moved.git"))
        line = lq.verdict(self.repos.clone, fetch=True)
        self.assertTrue(line.startswith("unknown: `git fetch origin main` failed"), line)

    def test_cli_prints_the_verdict_and_exits_by_it(self):
        self.repos.commit(QUEUE_SCRIPTS)
        self.repos.push_main()
        script = os.path.join(SCRIPTS, "land_queue.py")
        out = subprocess.run(["python3", script, "--fetch"], cwd=self.repos.clone, capture_output=True,
                             text=True, env=GIT_ENV)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "queue"))
        out = subprocess.run(["python3", script], cwd=self.repos.root, capture_output=True, text=True,
                             env=GIT_ENV)
        self.assertEqual(out.returncode, 1)
        self.assertTrue(out.stdout.startswith("unknown"), out.stdout)


class FakeResolver(gate.SkillResolver):
    ACTIONS = {"ship": ("merge",), "hotfix": ("dispatch", "merge")}

    def __init__(self):
        super().__init__("/repo")

    def actions(self, name):
        return self.ACTIONS.get(gate.bare(name), ())

    def land_queue_only(self, name):
        return gate.bare(name) == "ship"


class HookReadsTheRepositoryTest(unittest.TestCase):
    """The release gate's queue lock against a real clone: the session's cwd repo's
    origin/main, read without a network call."""

    def setUp(self):
        self.repos = Repos()
        self.addCleanup(self.repos.close)

    def decide(self, command, typed):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        content = f"<command-message>{typed}</command-message>\n<command-name>/{typed}</command-name>"
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "parentUuid": "u0", "uuid": "u1",
                                "message": {"role": "user", "content": content}}) + "\n")
        try:
            return gate.decide_bash(
                {"tool_name": "Bash", "tool_input": {"command": command}, "transcript_path": path,
                 "tool_use_id": "toolu_now", "cwd": self.repos.clone},
                FakeResolver(), sleep=lambda _s: None, branch_of=lambda _d: "old-branch",
                scope=gate.GUARD_ALL, history_scope=gate.GUARD_ALL)
        finally:
            os.unlink(path)

    def test_a_queue_on_main_refuses_the_ship_skills_bare_merge_from_an_old_branch(self):
        self.repos.commit({"lint": "eslint ."})
        self.repos.push_main()
        self.assertIsNone(self.decide("gh pr merge 12 --squash", "claude-tdd-workflow:ship"),
                          "no queue on main: the generic flow's merge is allowed")
        git(self.repos.clone, "checkout", "--quiet", "-b", "old-branch")
        other = os.path.join(self.repos.root, "other")
        subprocess.run(["git", "clone", "--quiet", self.repos.origin, other], check=True, env=GIT_ENV,
                       capture_output=True)
        with open(os.path.join(other, "package.json"), "w") as fh:
            json.dump({"name": "x", "scripts": QUEUE_SCRIPTS}, fh)
        git(other, "commit", "--quiet", "-am", "the queue lands")
        git(other, "push", "--quiet", "origin", "HEAD:main")
        git(self.repos.clone, "fetch", "--quiet", "origin", "main")
        reason = self.decide("gh pr merge 12 --squash --match-head-commit abc", "claude-tdd-workflow:ship")
        self.assertIn("land queue", reason)
        self.assertIn("git merge origin/main", reason)
        self.assertIsNone(self.decide("pnpm ship:land land --pr 12", "claude-tdd-workflow:ship"))
        self.assertIsNone(self.decide("gh pr merge 12 --merge", "hotfix"),
                          "a skill without `land-queue: only` keeps its merge")


if __name__ == "__main__":
    unittest.main()
