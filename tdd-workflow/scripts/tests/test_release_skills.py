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
# The land runs in the background at the Bash tool's ceiling (7,200,000 ms), and the
# queue's own deadline is held 200 s inside it so the exit is still read.
LAND = "pnpm ship:land land --pr <PR> --max-wait-ms 7000000"
BACKGROUND_CEILING = "7200000"


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
        for text, name in ((read(os.path.join(ROOT, "skills", "ship", "generic.md")), "ship"),
                           (skill("merge"), "merge")):
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


class LandQueueSkillsTest(unittest.TestCase):
    """Slice 803: where the repo has ship:gate and ship:land, the scripts own the
    mechanics and the queue is the only way onto main."""

    def test_ship_lands_through_the_queue_and_owns_none_of_its_mechanics(self):
        text = skill("ship")
        cmds = commands(text)
        self.assertIn("pnpm ship:gate --label ship", cmds)
        self.assertIn(LAND, cmds)
        self.assertIn("run_in_background: true", text)
        for step, end in (("4. **Gate.**", "5. **Push:**"), ("7. **Land**", "8. **The outcome")):
            self.assertIn(BACKGROUND_CEILING, section(text, step, end), f"{step} waits at the ceiling")
        for gone in ("gh pr merge", "gate-record.mjs", "gate-lock.mjs", "turbo run", "gate:checks",
                     "rev-list --count HEAD..origin/main", "wait-mergeable.py <PR>"):
            self.assertFalse([c for c in cmds if gone in c], f"the land-queue flow runs no `{gone}`")
        self.assertIn("${CLAUDE_SKILL_DIR}/generic.md", text, "a repo without the scripts keeps its flow")

    def test_ship_reads_every_exit_of_the_gate_and_the_land(self):
        text = skill("ship")
        gate_exits = section(text, "4. **Gate.**", "5. **Push:**")
        for code in ("0", "1", "2", "75"):
            self.assertRegex(gate_exits, rf"- `{code}`", f"ship:gate exit {code}")
        outcome = section(text, "8. **The outcome", "## After it lands")
        merge = section(skill("merge"), "3. **Report by exit code.**", "**Landing does NOT deploy.**")
        for code in ("0", "1", "2", "3", "4", "5", "75"):
            self.assertRegex(outcome, rf"- `{code}`", f"ship: ship:land exit {code}")
            self.assertRegex(merge, rf"- `{code}`", f"merge: ship:land exit {code}")
        self.assertIn("tell the owner", section(outcome, "- `5`", "- `75`"))
        for text_, name in ((outcome, "ship"), (merge, "merge")):
            exit_2 = " ".join(section(text_, "- `2`", "- `75`").split())
            self.assertIn("never report the PR as not merged", exit_2, f"{name}: exit 2 promises nothing")

    def test_the_model_only_adds_to_the_floor_with_a_reason(self):
        text = skill("ship")
        self.assertIn("--reason", text)
        self.assertIn("nothing narrows the floor", text)

    def test_quickship_and_merge_route_through_the_queue(self):
        quick = skill("quickship")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/ship/SKILL.md", section(quick, "## Land queue `yes`", "## Land queue `no`"))
        merge = section(skill("merge"), "## Land queue `yes`", "## Land queue `no`")
        self.assertIn(LAND, commands(merge))
        self.assertIn(BACKGROUND_CEILING, merge)
        self.assertFalse([c for c in commands(merge) if "gh pr merge" in c])
        # It deletes `<branch>` after a land, so it reads the head branch first.
        self.assertTrue([c for c in commands(merge) if c.startswith("gh pr view") and "headRefName" in c])

    def test_the_generic_flows_drive_none_of_the_land_queue_repos_gate_scripts(self):
        # A repo with ship:gate and ship:land always takes the queue path; no other repo
        # has these scripts, so a branch for them in the generic flow is unreachable.
        generic = read(os.path.join(ROOT, "skills", "ship", "generic.md"))
        quick = section(skill("quickship"), "## Land queue `no`")
        for text, name in ((generic, "ship/generic.md"), (quick, "quickship")):
            for gone in ("gate-record", "gate-lock", "gate:checks", "run-checks-parallel", "TURBO_SCM_BASE"):
                self.assertNotIn(gone, text, f"{name} names {gone}")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md", quick, "quickship runs the same gate")

    def test_deploy_joins_a_run_that_already_builds_the_commit(self):
        text = skill("deploy")
        cmds = commands(text)
        self.assertTrue([c for c in cmds if c.startswith("gh run list --workflow deploy.yml")
                         and 'select(.status != "completed")' in c])
        self.assertIn("git merge-base --is-ancestor <SHA> <RUN_SHA>", cmds)
        self.assertLess(text.index("git merge-base --is-ancestor"), text.index("git push origin deploy-<TS>"),
                        "the train is asked before a tag is pushed")
        self.assertNotIn("ci.yml", text, "the disabled CI workflow is not consulted")

    def test_verify_runs_the_gate_itself(self):
        for path in ("skills/feature/phases/04-verify.md", "skills/refactor/phases/05-verify.md"):
            text = read(os.path.join(ROOT, path))
            self.assertIn("pnpm ship:gate --label verify", text, path)
            self.assertIn("run_in_background: true", text, path)
            self.assertIn(BACKGROUND_CEILING, text, path)

    def test_invariant_runner_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "agents", "invariant-runner.md")))
        for path in glob.glob(os.path.join(ROOT, "**", "*"), recursive=True):
            if os.path.isfile(path) and path != os.path.abspath(__file__) and not path.endswith(".pyc"):
                self.assertNotIn("invariant-runner", read(path), path)


if __name__ == "__main__":
    unittest.main()
