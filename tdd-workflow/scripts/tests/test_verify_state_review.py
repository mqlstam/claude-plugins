"""VERIFY opens the PR with a marker naming the verdict that reviewed it; /ship lands
exactly the PR head that verdict covers. verify-state.py binds the two:

    verify-state.py review-marker       # the marker the PR body carries
    verify-state.py review-covers <PR>  # the PR head is the reviewed work, and its
                                        # marker names that verdict
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "verify-state.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        for key, value in (("user.email", "t@example.org"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.root, "config", key, value)
        (self.root / ".gitignore").write_text(".claude/\n")
        (self.root / "app.py").write_text("print('v1')\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "chore: base")
        git(self.root, "checkout", "-q", "-b", "feature")
        (self.root / "app.py").write_text("print('v2')\n")
        # A stand-in `gh` on PATH answers `pr view` from a file and logs its arguments.
        self.bin = base / "bin"
        self.bin.mkdir()
        self.pr_file, self.log = base / "pr.json", base / "gh-argv.jsonl"
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\nimport json, sys\n"
                      f"open({str(self.log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      f"text = open({str(self.pr_file)!r}).read()\n"
                      "if text == 'FAIL':\n    print('HTTP 502', file=sys.stderr); sys.exit(1)\n"
                      "print(text)\n")
        gh.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, env=env)

    def ok(self, *args):
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def refused(self, *args, says=()):
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for needle in says:
            self.assertIn(needle, result.stdout)
        return result.stdout

    def commit(self, message="feat: work"):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")


class ReviewCoversTest(Repo):
    """The PR that lands is exactly the reviewed work, and its marker names that review."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_script("record").returncode, 0)
        self.head = self.commit()

    def marker(self):
        out = self.ok("review-marker").strip()
        self.assertRegex(out, r"^<!-- verify-verdict: [0-9a-f]{64} -->$")
        return out

    def answer(self, head, body, state="OPEN"):
        self.pr_file.write_text(json.dumps({"state": state, "headRefOid": head, "body": body,
                                            "number": 42, "url": "https://github.com/o/r/pull/42"}))

    def test_the_marker_names_the_verdict_that_covers_head(self):
        marker = self.marker()
        state = json.loads((self.root / ".claude" / ".verify-state.json").read_text())
        self.assertIn(state["treeDigest"], marker)

    def test_no_marker_without_a_covering_verdict(self):
        (self.root / "app.py").write_text("print('unreviewed')\n")
        self.commit("feat: unreviewed")
        self.refused("review-marker", says=("not covered",))

    def test_the_reviewed_head_is_covered(self):
        self.answer(self.head, f"## Validation\n{self.marker()}\n")
        out = self.ok("review-covers", "42")
        self.assertIn(self.head[:12], out)
        argv = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(argv[-1][:3], ["pr", "view", "42"])

    def test_a_new_commit_after_the_review_is_named(self):
        # Mutation: comparing only the marker passes an unreviewed head.
        marker = self.marker()
        (self.root / "app.py").write_text("print('after review')\n")
        head = self.commit("fix: after the review")
        self.answer(head, f"## Validation\n{marker}\n")
        self.refused("review-covers", "42", says=("changed after the review", "app.py"))

    def test_a_second_verify_makes_the_marker_stale(self):
        # A fix was reviewed again: the PR still carries the first VERIFY's marker.
        marker = self.marker()
        (self.root / "app.py").write_text("print('fixed')\n")
        self.assertEqual(self.run_script("record").returncode, 0)
        head = self.commit("fix: the review finding")
        self.answer(head, f"## Validation\n{marker}\n")
        self.refused("review-covers", "42", says=("names an earlier VERIFY", "open it for review again"))

    def test_a_clean_integration_of_main_keeps_the_marker(self):
        marker = self.marker()
        git(self.root, "checkout", "-q", "main")
        (self.root / "main_only.py").write_text("m = 1\n")
        self.commit("feat: main moves")
        git(self.root, "checkout", "-q", "feature")
        git(self.root, "update-ref", "refs/remotes/origin/main", "main")
        git(self.root, "merge", "-q", "--no-edit", "-m", "chore: integrate origin/main before shipping", "origin/main")
        self.ok("integrate")
        self.answer(git(self.root, "rev-parse", "HEAD"), f"## Validation\n{marker}\n")
        self.ok("review-covers", "42")

    def test_a_body_without_the_marker_is_refused(self):
        self.answer(self.head, "## Validation\nnothing\n")
        self.refused("review-covers", "42", says=("carries no verify-verdict marker",))

    def test_a_pr_that_is_not_open_is_refused(self):
        self.answer(self.head, self.marker(), state="MERGED")
        self.refused("review-covers", "42", says=("MERGED",))

    def test_a_head_that_is_not_fetched_is_named(self):
        self.answer("0" * 40, self.marker())
        self.refused("review-covers", "42", says=("not in this clone", "git fetch"))

    def test_an_unreadable_pr_is_refused(self):
        self.pr_file.write_text("FAIL")
        self.refused("review-covers", "42", says=("HTTP 502",))


if __name__ == "__main__":
    unittest.main()
