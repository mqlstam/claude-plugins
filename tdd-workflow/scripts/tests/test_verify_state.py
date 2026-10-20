"""verify-state.py binds the VERIFY verdict to the reviewed contents, not to HEAD."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "verify-state.py"


def run(cwd, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True
    )


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class VerifyStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.org")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text("ignored/\n")
        (self.root / "app.py").write_text("print('v1')\n")
        (self.root / "old.py").write_text("x = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        # Reviewed work in progress: one modified, one new, one deleted file.
        (self.root / "app.py").write_text("print('v2')\n")
        (self.root / "new.py").write_text("y = 2\n")
        (self.root / "old.py").unlink()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self):
        result = run(self.root, "record")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_check(self, code, *args):
        flags = []
        needles = list(args)
        if needles[:1] == ["--commit"]:
            flags, needles = needles[:2], needles[2:]
        result = run(self.root, "check", *flags)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stdout)

    def test_unchanged_contents_pass(self):
        self.record()
        self.assert_check(0)

    def test_committing_the_reviewed_work_keeps_the_verdict(self):
        self.record()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "ship it")
        self.assert_check(0)

    def test_an_uncommitted_edit_after_review_is_refused_and_named(self):
        self.record()
        (self.root / "app.py").write_text("print('changed after review')\n")
        self.assert_check(1, "modified: app.py")

    def test_a_new_untracked_file_is_refused(self):
        self.record()
        (self.root / "extra.py").write_text("z = 3\n")
        self.assert_check(1, "added: extra.py")

    def test_a_deleted_file_is_refused(self):
        self.record()
        (self.root / "new.py").unlink()
        self.assert_check(1, "deleted: new.py")

    def test_a_mode_change_is_refused(self):
        self.record()
        os.chmod(self.root / "app.py", 0o755)
        self.assert_check(1, "modified: app.py")

    def test_ignored_files_do_not_matter(self):
        self.record()
        (self.root / "ignored").mkdir()
        (self.root / "ignored" / "build.log").write_text("noise\n")
        self.assert_check(0)

    def test_the_state_file_itself_does_not_invalidate_the_verdict(self):
        # Where .claude/ is not ignored, writing the state must not change the contents.
        self.record()
        self.assert_check(0)

    def test_a_rename_is_followed(self):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "mv", "new.py", "renamed.py")
        self.record()
        self.assert_check(0)
        (self.root / "renamed.py").write_text("y = 3\n")
        self.assert_check(1, "modified: renamed.py")

    def test_the_commit_check_passes_when_the_reviewed_work_is_committed(self):
        self.record()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "ship it")
        self.assert_check(0, "--commit", "HEAD")

    def test_a_stale_index_cannot_commit_unreviewed_contents(self):
        # Stage a defect, restore the reviewed copy on disk without restaging it:
        # the working tree passes, the commit must not.
        (self.root / "app.py").write_text("raise SystemExit('defect')\n")
        git(self.root, "add", "app.py")
        (self.root / "app.py").write_text("print('v2')\n")
        self.record()
        self.assert_check(0)
        git(self.root, "add", "new.py", "old.py")
        git(self.root, "commit", "-q", "-m", "ship it")
        self.assert_check(1, "--commit", "HEAD", "modified: app.py")

    def test_a_staged_deletion_of_a_present_ignored_file_is_refused_at_commit(self):
        (self.root / "keep.cfg").write_text("reviewed\n")
        git(self.root, "add", "keep.cfg")
        git(self.root, "commit", "-q", "-m", "base")
        self.record()
        git(self.root, "rm", "-q", "--cached", "keep.cfg")
        (self.root / ".gitignore").write_text("ignored/\nkeep.cfg\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "ship it")
        self.assert_check(1, "--commit", "HEAD", "deleted: keep.cfg")

    def test_assume_unchanged_is_refused(self):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "update-index", "--assume-unchanged", "app.py")
        (self.root / "app.py").write_text("print('hidden from status')\n")
        result = run(self.root, "record")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("assume-unchanged or skip-worktree", result.stdout)
        self.assertIn("app.py", result.stdout)

    def test_skip_worktree_is_refused(self):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "update-index", "--skip-worktree", "app.py")
        (self.root / "app.py").write_text("print('hidden from status')\n")
        result = run(self.root, "record")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("app.py", result.stdout)

    def test_missing_state_is_refused(self):
        self.assert_check(1, "missing")

    def test_a_head_only_state_from_an_older_verify_is_refused(self):
        state = self.root / ".claude" / ".verify-state.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"headSha": "abc", "verdict": "READY"}))
        self.assert_check(1, "predates the current format")


class SubmoduleTest(unittest.TestCase):
    """A submodule is bound to the commit it has checked out."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.lib = base / "lib"
        self.root = base / "app"
        for repo in (self.lib, self.root):
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.email", "t@example.org")
            git(repo, "config", "user.name", "t")
            git(repo, "config", "commit.gpgsign", "false")
        (self.lib / "lib.py").write_text("v = 1\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-q", "-m", "lib")
        git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(self.lib), "sub")
        git(self.root, "commit", "-q", "-m", "with submodule")
        self.sub = self.root / "sub"
        for key, value in (("user.email", "t@example.org"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.sub, "config", key, value)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_moved_submodule_commit_is_refused(self):
        self.assertEqual(run(self.root, "record").returncode, 0)
        (self.sub / "lib.py").write_text("v = 2\n")
        git(self.sub, "commit", "-q", "-am", "moved")
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("modified: sub", result.stdout)

    def test_a_dirty_submodule_is_refused(self):
        self.assertEqual(run(self.root, "record").returncode, 0)
        (self.sub / "scratch.py").write_text("x = 1\n")
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("submodule sub has uncommitted changes", result.stdout)

    def test_committing_the_reviewed_gitlink_keeps_the_verdict(self):
        (self.sub / "lib.py").write_text("v = 2\n")
        git(self.sub, "commit", "-q", "-am", "moved")
        self.assertEqual(run(self.root, "record").returncode, 0)
        git(self.root, "add", "sub")
        git(self.root, "commit", "-q", "-m", "bump")
        result = run(self.root, "check", "--commit", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout)


class Sha256SymlinkTest(unittest.TestCase):
    def test_a_committed_symlink_keeps_the_verdict_in_a_sha256_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "--object-format=sha256", "-b", "main")
            git(root, "config", "user.email", "t@example.org")
            git(root, "config", "user.name", "t")
            git(root, "config", "commit.gpgsign", "false")
            (root / "target.txt").write_text("t\n")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "base")
            os.symlink("target.txt", root / "link")
            self.assertEqual(run(root, "record").returncode, 0)
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "link")
            result = run(root, "check")
            self.assertEqual(result.returncode, 0, result.stdout)
            result = run(root, "check", "--commit", "HEAD")
            self.assertEqual(result.returncode, 0, result.stdout)


class IntegrationTest(unittest.TestCase):
    """Merging main in is accepted only when it is exactly git's clean merge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        for key, value in (("user.email", "t@example.org"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.root, "config", key, value)
        (self.root / ".gitignore").write_text(".claude/\n")
        (self.root / "shared.py").write_text("a = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "checkout", "-q", "-b", "feature")
        (self.root / "feature.py").write_text("f = 1\n")
        self.assertEqual(run(self.root, "record").returncode, 0)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "feature")
        git(self.root, "checkout", "-q", "main")
        (self.root / "main_only.py").write_text("m = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "main moves")
        git(self.root, "checkout", "-q", "feature")

    def tearDown(self):
        self.tmp.cleanup()

    def publish_main(self):
        # Stands in for `git fetch origin main`: integrate accepts only the fetched main.
        git(self.root, "update-ref", "refs/remotes/origin/main", "main")

    def merge_main(self):
        self.publish_main()
        git(self.root, "merge", "-q", "--no-edit", "-m", "chore: integrate", "origin/main")

    def test_a_clean_integration_is_accepted_and_survives_a_resumed_release(self):
        self.merge_main()
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(run(self.root, "check", "--commit", "HEAD").returncode, 0)
        # A release resumed later starts with the working-tree check.
        self.assertEqual(run(self.root, "check").returncode, 0)

    def test_a_change_after_the_merge_is_named_for_review(self):
        self.merge_main()
        (self.root / "hook_added.py").write_text("h = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--amend", "--no-edit")
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("hook_added.py", result.stdout)
        result = run(self.root, "check", "--commit", "HEAD")
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_a_commit_on_top_of_an_accepted_integration_is_refused(self):
        self.merge_main()
        self.assertEqual(run(self.root, "integrate").returncode, 0)
        (self.root / "later.py").write_text("l = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "a hook's commit")
        self.assertEqual(run(self.root, "check", "--commit", "HEAD").returncode, 1)

    def test_a_conflict_needs_a_targeted_review(self):
        git(self.root, "checkout", "-q", "main")
        (self.root / "shared.py").write_text("a = 'main'\n")
        git(self.root, "commit", "-q", "-am", "main edits shared")
        git(self.root, "checkout", "-q", "feature")
        (self.root / "shared.py").write_text("a = 'feature'\n")
        self.assertEqual(run(self.root, "record").returncode, 0)
        git(self.root, "commit", "-q", "-am", "feature edits shared")
        self.publish_main()
        subprocess.run(["git", "merge", "--no-edit", "origin/main"], cwd=self.root, capture_output=True)
        (self.root / "shared.py").write_text("a = 'resolved'\n")
        # The resolution also changes a file that did not conflict.
        (self.root / "feature.py").write_text("f = 'adjusted for the resolution'\n")
        git(self.root, "add", "shared.py", "feature.py")
        git(self.root, "commit", "-q", "--no-edit")
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("conflicted: shared.py", result.stdout)
        self.assertIn("edited in the resolution: feature.py", result.stdout)

    def test_a_second_integration_chains_on_the_first(self):
        self.merge_main()
        self.assertEqual(run(self.root, "integrate").returncode, 0)
        git(self.root, "checkout", "-q", "main")
        (self.root / "main_again.py").write_text("m = 2\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "main moves again")
        git(self.root, "checkout", "-q", "feature")
        self.merge_main()
        self.assertEqual(run(self.root, "integrate").returncode, 0)
        self.assertEqual(run(self.root, "check", "--commit", "HEAD").returncode, 0)

    def test_merging_another_branch_is_not_an_integration(self):
        self.publish_main()
        git(self.root, "checkout", "-q", "-b", "colleague", "main")
        (self.root / "unreviewed.py").write_text("u = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "a colleague's unreviewed work")
        git(self.root, "checkout", "-q", "feature")
        git(self.root, "merge", "-q", "--no-edit", "-m", "merge colleague", "colleague")
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not part of refs/remotes/origin/main", result.stdout)
        self.assertEqual(run(self.root, "check", "--commit", "HEAD").returncode, 1)

    def test_an_up_to_date_merge_needs_nothing(self):
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD~1")
        subprocess.run(["git", "merge", "--no-edit", "origin/main"], cwd=self.root, capture_output=True)
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("nothing to integrate", result.stdout)

    def test_another_sessions_fetch_does_not_invalidate_the_integration(self):
        self.merge_main()
        # A sibling worktree fetches a newer main before this one runs integrate.
        git(self.root, "checkout", "-q", "main")
        (self.root / "newer.py").write_text("n = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "main moves on")
        self.publish_main()
        git(self.root, "checkout", "-q", "feature")
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_reviewed_merge_commit_needs_no_integration(self):
        self.merge_main()
        self.assertEqual(run(self.root, "record").returncode, 0)
        subprocess.run(["git", "merge", "--no-edit", "origin/main"], cwd=self.root, capture_output=True)
        result = run(self.root, "integrate")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("nothing to integrate", result.stdout)

    def test_an_unreadable_verdict_does_not_hide_a_valid_one_elsewhere(self):
        self.merge_main()
        self.assertEqual(run(self.root, "integrate").returncode, 0)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip()
        other = Path(self.tmp.name) / "other"
        git(self.root, "worktree", "add", "-q", "--detach", str(other), "main")
        (other / ".claude").mkdir()
        (other / ".claude" / ".verify-state.json").write_text('{"verdict": "REA')
        result = run(other, "check", "--commit", head)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_merge_from_another_checkout_finds_the_verdict(self):
        self.merge_main()
        self.assertEqual(run(self.root, "integrate").returncode, 0)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip()
        other = Path(self.tmp.name) / "other"
        git(self.root, "worktree", "add", "-q", "--detach", str(other), "main")
        result = run(other, "check", "--commit", head)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("verdict from", result.stdout)


if __name__ == "__main__":
    unittest.main()
