"""The release skills tell the model to run commands the plugin's own hooks accept."""
import glob
import importlib.util
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SPEC = importlib.util.spec_from_file_location("guard", os.path.join(ROOT, "scripts", "bash-shape-guard.py"))
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

COMMAND_START = re.compile(r"^(gh|git|python3|node|pnpm|date)\b")
RELEASE_SKILLS = ("ship", "merge", "deploy", "quickship")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def skill(name):
    return read(os.path.join(ROOT, "skills", name, "SKILL.md"))


def skill_files(name):
    """SKILL.md and every supporting file the skill sends the model to."""
    return sorted(glob.glob(os.path.join(ROOT, "skills", name, "**", "*.md"), recursive=True))


def commands(text):
    """Fenced bash blocks and inline code spans that are commands to run."""
    body = text.split("\n---", 1)[1] if text.startswith("---") else text
    out = re.findall(r"```bash\n(.*?)```", body, re.S)
    for span in re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", body, flags=re.S)):
        if COMMAND_START.match(span):
            out.append(span)
    return out


def section(text, start, end=None):
    i = text.index(start)
    j = text.index(end, i) if end else len(text)
    return text[i:j]


class ReleaseSkillsTest(unittest.TestCase):
    def test_every_command_passes_the_shape_guard(self):
        for name in RELEASE_SKILLS:
            for path in skill_files(name):
                for cmd in commands(read(path)):
                    self.assertIsNone(guard.decide({"tool_input": {"command": cmd}}), f"{path}: {cmd}")

    def test_no_merge_deletes_the_branch_through_gh(self):
        # `gh pr merge --delete-branch` fails in a worktree AFTER a successful merge.
        for name in ("ship", "merge"):
            for path in skill_files(name):
                for cmd in commands(read(path)):
                    if "gh pr merge" in cmd:
                        self.assertNotIn("--delete-branch", cmd, path)
                        self.assertIn("--match-head-commit", cmd, path)

    def test_generic_merges_wait_for_the_settled_state_and_read_the_verdict_from_the_pr(self):
        for text, name in ((skill("ship"), "ship"),
                           (skill("merge"), "merge")):
            self.assertIn("scripts/wait-mergeable.py <PR> --head <SHA>", text, name)
            self.assertIn("run_in_background: true", text, name)
            self.assertRegex(text, r"gh pr view <PR> --json state[^`]*mergeCommit", name)
            self.assertIn("git/refs/heads/<BRANCH>", text, name)

    def test_deploy_waits_for_the_verdict_inside_the_turn(self):
        # Owner, 2026-10-20: run 39493655874 was refused for billing 2 s after its T3
        # thread settled, and nobody noticed. The run is watched to its end.
        text = skill("deploy")
        cmds = commands(text)
        self.assertFalse([c for c in cmds if c.startswith("gh run watch")], "no gh run watch")
        self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py find deploy.yml <SHA>", cmds)
        self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py await <id> --deadline-min 190 "
                      "--slice-min 9", cmds)
        self.assertIn("gh run view <id> --json status,conclusion,headSha", cmds)
        self.assertIn("git push origin deploy-<TS>", cmds)
        self.assertNotIn("end your turn", text)
        self.assertIn("T3 Code", text)
        self.assertIn("reason=billing", text)
        # At the bound the run is open, never done, and stays visible.
        self.assertIn("release_run_await_deadline NOT CONCLUDED", text)
        self.assertIn("pnpm release:status", text)
        self.assertIn("Open release", text)


class LocalValidationSkillsTest(unittest.TestCase):
    """Generic repositories retain assertion-only validation and release waits."""

    def test_generic_runs_the_assertions_never_the_writers_or_aggregates(self):
        # M5: every check:*/env:* minus `:update` still ran env:sync, which rewrites
        # .env.example, and aggregates on top of their members.
        step = section(read(os.path.join(ROOT, "skills", "ship", "generic.md")),
                       "5. **Project invariants", "Treat any failure")
        self.assertIn("`pnpm gate:checks`", step, "the repo's own sweep comes first")
        self.assertIn("nothing else for this step", step)
        for word in ("`update`", "`sync`", "`fix`", "`write`", "`env:sync`", "`--write`", "`--fix`"):
            self.assertIn(word, step, f"a writer is named by {word}")
        self.assertIn("**an aggregate**", step)
        self.assertIn("printed", step, "what the rule drops is said out loud")
        self.assertIn("`jq .scripts package.json`", read(os.path.join(ROOT, "skills", "ship", "generic.md")),
                      "step 3 lists the commands step 5 reads")

    def test_deploy_joins_a_run_that_already_builds_the_commit(self):
        text = skill("deploy")
        cmds = commands(text)
        self.assertTrue([c for c in cmds if c.startswith("gh run list --workflow deploy.yml")
                         and 'select(.status != "completed")' in c])
        self.assertIn("git merge-base --is-ancestor <SHA> <RUN_SHA>", cmds)
        self.assertLess(text.index("git merge-base --is-ancestor"), text.index("git push origin deploy-<TS>"),
                        "the train is asked before a tag is pushed")
        self.assertNotIn("ci.yml", text, "the disabled CI workflow is not consulted")

    def test_invariant_runner_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "agents", "invariant-runner.md")))
        for path in glob.glob(os.path.join(ROOT, "**", "*"), recursive=True):
            if os.path.isfile(path) and path != os.path.abspath(__file__) and not path.endswith(".pyc"):
                self.assertNotIn("invariant-runner", read(path), path)


if __name__ == "__main__":
    unittest.main()
