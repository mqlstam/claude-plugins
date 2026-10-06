"""Tests for worktree-session-end.sh, the plugin's SessionEnd dispatcher.

It is fed the payload Claude Code actually sends at SessionEnd (measured on
v2.1.289): `cwd` is the MAIN checkout, CLAUDE_PROJECT_DIR is the worktree.
Run: python3 -m unittest discover -s scripts/tests
"""
import json
import os
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "worktree-session-end.sh")


class SessionEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.main = os.path.join(self.tmp, "repo")
        self.wt = os.path.join(self.main, ".claude", "worktrees", "alpha")
        os.makedirs(os.path.join(self.main, "scripts"))
        self.record = os.path.join(self.tmp, "record.txt")
        # A recording stand-in for each repo script the dispatcher may start.
        for name in ("worktree-retire.sh", "worktree-reap-orphans.sh"):
            with open(os.path.join(self.main, "scripts", name), "w") as fh:
                fh.write(f'#!/usr/bin/env bash\nprintf "{name} %s\\n" "$*" >> "{self.record}"\n')
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.tmp])

    def make_worktree_with_softstop(self):
        os.makedirs(os.path.join(self.wt, "scripts"))
        with open(os.path.join(self.wt, "scripts", "worktree-soft-stop.sh"), "w") as fh:
            fh.write(f'#!/usr/bin/env bash\nprintf "soft-stop %s\\n" "$(cat)" >> "{self.record}"\n')

    def run_hook(self, project_dir, reason="prompt_input_exit", extra_env=None):
        payload = {"session_id": "s1", "cwd": self.main, "hook_event_name": "SessionEnd", "reason": reason}
        env = {**os.environ, "HOME": self.home, "CLAUDE_PROJECT_DIR": project_dir, **(extra_env or {})}
        env.pop("ENDOXIA_WORKTREE_RETIRE_SCRIPT", None)
        res = subprocess.run(["bash", SCRIPT], input=json.dumps(payload), env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)

    def recorded(self, wait_for=None):
        deadline = time.time() + 5
        while True:
            text = open(self.record).read() if os.path.exists(self.record) else ""
            if wait_for is None or wait_for in text or time.time() > deadline:
                return text
            time.sleep(0.1)

    def test_removed_worktree_is_retired(self):
        self.run_hook(self.wt)  # the directory does not exist: "Remove"
        self.assertIn(f"worktree-retire.sh --dir {self.wt}", self.recorded(wait_for="worktree-retire.sh"))

    def test_kept_worktree_is_soft_stopped_with_cwd_corrected(self):
        self.make_worktree_with_softstop()
        self.run_hook(self.wt)
        line = self.recorded(wait_for="soft-stop")
        payload = json.loads(line.split("soft-stop ", 1)[1])
        self.assertEqual(payload["cwd"], self.wt, "the stop script must see the worktree, not the main checkout")
        self.assertNotIn("worktree-retire.sh", line)

    def test_old_main_checkout_falls_back_to_the_reaper(self):
        os.remove(os.path.join(self.main, "scripts", "worktree-retire.sh"))
        self.run_hook(self.wt)
        self.assertIn("worktree-reap-orphans.sh", self.recorded(wait_for="worktree-reap-orphans.sh"))

    def test_main_checkout_and_other_repos_are_left_alone(self):
        for project in (self.main, "/somewhere/else", ""):
            self.run_hook(project)
        self.run_hook(os.path.join(self.wt, "nested"))  # not directly under .claude/worktrees
        time.sleep(0.5)
        self.assertEqual(self.recorded(), "")

    def test_clear_and_resume_do_nothing(self):
        for reason in ("clear", "resume"):
            self.run_hook(self.wt, reason=reason)
        time.sleep(0.5)
        self.assertEqual(self.recorded(), "")


if __name__ == "__main__":
    unittest.main()
