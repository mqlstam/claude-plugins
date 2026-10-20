"""Slice 826: direct authorized merges, local fast checks and staging health."""
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_release_approval_gate import FakeResolver, Transcript, gate, no_github, no_sleep, on_branch
from test_release_skills import ROOT, commands, read, skill

INTEGRATION_COMMAND = 'git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main'


class DirectReleaseTest(unittest.TestCase):
    def test_local_integration_is_allowed_without_spending_release_authorization(self):
        from test_release_approval_gate import bash_decision
        transcript = Transcript().typed("approve", "merge")
        self.assertIsNone(bash_decision(transcript, INTEGRATION_COMMAND))
        transcript.bash(INTEGRATION_COMMAND)
        self.assertIsNone(bash_decision(
            transcript, "gh pr merge 42 --squash --match-head-commit abc"))

    def test_authorized_merge_needs_no_remote_main_policy(self):
        # The checkout may predate the new scripts. Authorization comes from the
        # user's transcript, never from origin/main's package.json or local refs.
        for typed, args in (("claude-tdd-workflow:ship", ""), ("approve", "merge")):
            transcript = Transcript().typed(typed, args)
            path = transcript.write()
            try:
                with patch.object(gate.subprocess, "run", side_effect=AssertionError("unexpected git/API probe")):
                    reason = gate.decide_bash(
                        {"tool_input": {"command": "gh pr merge 42 --squash --match-head-commit abc"},
                         "transcript_path": path, "cwd": "/repo"},
                        FakeResolver(), sleep=no_sleep, branch_of=on_branch("feature"),
                        scope=gate.GUARD_ALL, history_scope=gate.GUARD_ALL, run_info=no_github)
                self.assertIsNone(reason)
            finally:
                os.unlink(path)

    def test_failed_direct_merge_preserves_approval_success_consumes_it(self):
        from test_release_approval_gate import bash_decision
        command = "gh pr merge 42 --squash --match-head-commit abc"
        transcript = Transcript().typed("approve", "merge")
        transcript.bash(command, error=True, output="Exit code 1\nhead changed")
        self.assertIsNone(bash_decision(transcript, command))
        transcript.bash(command)
        self.assertIsNotNone(bash_decision(transcript, command))

    def test_background_merge_holds_approval_until_a_trusted_failure(self):
        from test_release_approval_gate import bash_decision
        command = "gh pr merge 42 --squash --match-head-commit abc"
        for status in (None, "completed", "killed", "stopped", "unknown"):
            transcript = Transcript().typed("approve", "merge")
            task = transcript.bash(command, background=True)
            if status:
                transcript.notify(task, status, 0)
            self.assertIsNotNone(bash_decision(transcript, command), status)
        transcript = Transcript().typed("approve", "merge")
        task = transcript.bash(command, background=True)
        transcript.notify("other-task", "failed", 1)
        transcript.notify(task, "failed", 1, sidechain=True)
        self.assertIsNotNone(bash_decision(transcript, command))
        transcript.notify(task, "failed", 1)
        self.assertIsNone(bash_decision(transcript, command))

    def test_refused_background_start_preserves_the_approval(self):
        from test_release_approval_gate import bash_decision
        command = "gh pr merge 42 --squash --match-head-commit abc"
        transcript = Transcript().typed("approve", "merge")
        transcript.bash(command, background=True, error=True, output="Blocked before running")
        self.assertIsNone(bash_decision(transcript, command))


class ReleaseRedesignContractTest(unittest.TestCase):
    def test_version_and_no_retired_mechanisms(self):
        manifest = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(manifest["version"], "1.6.4")
        for relative in ("skills/ship/SKILL.md", "skills/ship/generic.md", "skills/merge/SKILL.md", "skills/quickship/SKILL.md",
                         "skills/feature/phases/04-verify.md", "skills/refactor/phases/05-verify.md",
                         "skills/tdd-methodology/SKILL.md", "scripts/instance-manager.py",
                         "scripts/release-approval-gate.py"):
            text = read(os.path.join(ROOT, relative))
            for retired in ("ship:gate", "ship:land", "gate-lock", "gate-record", "land_queue", "land-queue", "FIFO"):
                self.assertNotIn(retired, text, relative)

    def test_ship_checks_verify_before_merging_main_then_selects_local_fast_lane(self):
        text = skill("ship")
        self.assertLess(text.index(".verify-state.json"), text.index(INTEGRATION_COMMAND))
        self.assertLess(text.index(INTEGRATION_COMMAND), text.index("pnpm ship:check"))
        self.assertIn("checkout's own `package.json`", text)
        self.assertIn("--add-tests", text)
        self.assertIn("--reason", text)
        self.assertNotIn("--force-with-lease", text)
        self.assertNotIn("git rebase", text)
        self.assertIn("gh pr merge <PR> --squash --match-head-commit <SHA>", text)

    def test_deploy_reads_exact_sha_health_before_join_or_tag(self):
        text = skill("deploy")
        self.assertIn("repos/:owner/:repo/commits/<SHA>/statuses", text)
        self.assertIn("endoxia/main-health", text)
        self.assertIn("failure", text)
        self.assertIn("pending", text)
        self.assertLess(text.index("endoxia/main-health"), text.index("**A run that already builds it.**"))

    def test_verify_uses_fast_check_and_has_no_rearming_on_main_merge(self):
        text = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn("pnpm ship:check", text)
        self.assertIn("before merging main", text)
        self.assertIn("generic.md", text)
        refactor = read(os.path.join(ROOT, "skills/refactor/phases/05-verify.md"))
        self.assertIn("skills/feature/phases/04-verify.md", refactor)

    def test_agents_can_bring_up_their_own_stack(self):
        # Owner, 2026-10-20: an agent working a goal brings up its stack itself
        # instead of waiting for a person to type /spinup.
        text = read(os.path.join(ROOT, "skills/spinup/SKILL.md"))
        self.assertNotIn("disable-model-invocation", text.split("\n---", 1)[0])
        verify = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn("/spinup", verify)
        self.assertNotIn("worktree-stack.sh down", verify)

    def test_codex_review_runs_at_high_reasoning_effort(self):
        # Owner, 2026-10-20: the Codex reviewer runs at high; T3's default is low.
        verify = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn('options: [{ id: "reasoningEffort", value: "high" }]', verify)

    def test_verify_launches_only_agents_that_exist(self):
        text = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        for agent in ("correctness-reviewer", "runtime-verifier"):
            self.assertIn(agent, text)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "agents", agent + ".md")), agent)
        retired = ("code-quality-reviewer", "jit-e2e-author", "rehydration-checker",
                   "temporal-checker", "dead-code-finder")
        for directory, _, files in os.walk(os.path.join(ROOT, "skills")):
            for name in files:
                body = read(os.path.join(directory, name))
                for agent in retired:
                    self.assertNotIn(f"`{agent}`", body, os.path.join(directory, name))
        for agent in retired:
            self.assertFalse(os.path.exists(os.path.join(ROOT, "agents", agent + ".md")), agent)


class MergePreparationContractTest(unittest.TestCase):
    def test_existing_pr_preparation_stays_under_merge_authorization(self):
        from test_release_approval_gate import bash_decision, skill_decision
        # A typed merge authorizes its own merge command, not a nested ship.
        transcript = Transcript().typed("merge")
        self.assertIsNotNone(skill_decision(transcript, "ship"))
        self.assertIsNone(bash_decision(
            transcript, "gh pr merge 42 --squash --match-head-commit abc"))
        text = skill("merge")
        self.assertNotRegex(text, r"(?:run|invoke) /ship\b")
        self.assertNotIn("gh pr create", text)
        self.assertIn(INTEGRATION_COMMAND, text)
        self.assertIn("git push origin <BRANCH>", text)
        self.assertIn("headRefOid", text)
        self.assertIn("skills/ship/generic.md", text)
        self.assertLess(text.index(".verify-state.json"),
                        text.index(INTEGRATION_COMMAND))
        self.assertLess(text.index("git push origin <BRANCH>"),
                        text.index("**Wait for GitHub"))


class ConventionalIntegrationTest(unittest.TestCase):
    """Exercise published commands through Git's real commit-msg hook boundary."""

    def repository(self, path):
        # No network, user hooks, signing agent or inherited worktree Git vars.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})

        def git(*args):
            # Same per-invocation budget as the release hook (hooks/hooks.json).
            return subprocess.run(["git", "-C", str(path), *args], env=env,
                                  text=True, capture_output=True, timeout=30)

        def succeed(*args):
            result = git(*args)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result

        succeed("init", "--initial-branch=main")
        succeed("config", "user.name", "Integration fixture")
        succeed("config", "user.email", "fixture@example.invalid")
        hook = path / ".git" / "hooks" / "commit-msg"
        hook.write_text(r"""#!/bin/sh
message=$(head -n 1 "$1")
printf '%s\n' "$message" >> .git/commit-hook-subjects
if ! printf '%s\n' "$message" | grep -Eq '^(feat|fix|refactor|chore|docs|test|ci|style|perf|build|revert)(\(.+\))?: .+'; then
  echo 'strict conventional commit hook refused the subject' >&2
  exit 1
fi
""")
        hook.chmod(0o755)
        (path / "base.txt").write_text("base\n")
        succeed("add", "base.txt")
        succeed("commit", "-m", "chore: create fixture")
        succeed("checkout", "-b", "feature")
        (path / "feature.txt").write_text("feature\n")
        succeed("add", "feature.txt")
        succeed("commit", "-m", "feat: add feature")
        succeed("checkout", "main")
        (path / "main.txt").write_text("main advanced\n")
        succeed("add", "main.txt")
        succeed("commit", "-m", "feat: advance main")
        succeed("update-ref", "refs/remotes/origin/main", "HEAD")
        succeed("checkout", "feature")
        return git

    def test_default_merge_subject_is_rejected_by_a_strict_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            git = self.repository(path)
            result = git("merge", "--no-edit", "origin/main")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("strict conventional commit hook refused", result.stderr)
            self.assertIn("Merge remote-tracking branch", (path / ".git" / "commit-hook-subjects").read_text())

    def test_published_integration_commands_merge_with_hooks_enabled(self):
        for relative in ("skills/ship/SKILL.md", "skills/ship/generic.md",
                         "skills/merge/SKILL.md", "skills/quickship/SKILL.md"):
            text = read(os.path.join(ROOT, relative))
            integrations = [command for command in commands(text)
                            if command.startswith("git merge ") and "origin/main" in command]
            self.assertTrue(integrations, relative)
            for command in integrations:
                with self.subTest(skill=relative, command=command), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)
                    git = self.repository(path)
                    result = git(*shlex.split(command)[1:])
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    subject = git("log", "-1", "--format=%s").stdout.strip()
                    self.assertEqual(subject, "chore: integrate origin/main before shipping")
                    self.assertEqual(git("rev-list", "--parents", "-n", "1", "HEAD").stdout.count(" "), 2)
                    self.assertEqual((path / ".git" / "commit-hook-subjects").read_text().splitlines()[-1], subject)
                    self.assertEqual(git("merge-base", "--is-ancestor", "origin/main", "HEAD").returncode, 0)
                    self.assertEqual(git("status", "--porcelain").stdout, "")
