"""The release skills tell the model to run commands the plugin's own hooks accept."""
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


def skill(name):
    with open(os.path.join(ROOT, "skills", name, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


def commands(text):
    """Fenced bash blocks and inline code spans that are commands to run."""
    body = text.split("\n---", 1)[1]
    out = re.findall(r"```bash\n(.*?)```", body, re.S)
    for span in re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", body, flags=re.S)):
        if COMMAND_START.match(span):
            out.append(span)
    return out


class ReleaseSkillsTest(unittest.TestCase):
    def test_every_command_passes_the_shape_guard(self):
        for name in ("ship", "merge", "deploy", "quickship"):
            for cmd in commands(skill(name)):
                self.assertIsNone(guard.decide({"tool_input": {"command": cmd}}), f"{name}: {cmd}")

    def test_no_merge_deletes_the_branch_through_gh(self):
        # `gh pr merge --delete-branch` fails in a worktree AFTER a successful merge.
        for name in ("ship", "merge"):
            for cmd in commands(skill(name)):
                if "gh pr merge" in cmd:
                    self.assertNotIn("--delete-branch", cmd, name)
                    self.assertIn("--match-head-commit", cmd, name)

    def test_merges_wait_for_the_settled_state_and_read_the_verdict_from_the_pr(self):
        for name in ("ship", "merge"):
            text = skill(name)
            self.assertIn("scripts/wait-mergeable.py <PR> --head <SHA>", text, name)
            self.assertIn("run_in_background: true", text, name)
            self.assertRegex(text, r"gh pr view <PR> --json state[^`]*mergeCommit", name)
            self.assertIn("git/refs/heads/<BRANCH>", text, name)

    def test_deploy_hands_back_at_the_run_id(self):
        text = skill("deploy")
        cmds = commands(text)
        self.assertFalse([c for c in cmds if c.startswith("gh run watch")], "no foreground watch")
        self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py find deploy.yml <SHA>", cmds)
        self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py await <id> --deadline-min 190", cmds)
        self.assertIn("gh run view <id> --json status,conclusion", cmds)
        self.assertIn("git push origin deploy-<TS>", cmds)


if __name__ == "__main__":
    unittest.main()
