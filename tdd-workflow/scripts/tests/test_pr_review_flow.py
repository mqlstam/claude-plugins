"""Owner, 2026-10-22: the owner approves a feature by watching its evidence on the PR
BEFORE it merges. VERIFY opens the PR (phase 1, no approval: nothing merges); /ship
lands exactly that PR head (phase 2, the approval as before)."""
import json
import os
import re
import unittest

from test_release_approval_gate import Transcript, bash_decision, gate, scope, skill_decision
from test_release_skills import ROOT, commands, read, skill

PLUGIN = "/p/claude-tdd-workflow/1.9.0"
OPEN = os.path.join(ROOT, "skills", "ship", "open-for-review.md")
VERIFY = os.path.join(ROOT, "skills", "feature", "phases", "04-verify.md")
SESSION = scope(session=("/work/endoxia",))


def concrete(cmd):
    """A documented command with its placeholders filled in as a session would."""
    for placeholder, value in (("${CLAUDE_PLUGIN_ROOT}", PLUGIN), ("<BRANCH>", "t3/feature-x"), ("<PR>", "1408"),
                               ('"<title>"', "feat-x"), ("./<path>", "./.claude/verify-evidence/r1/02-flow.mp4"),
                               ("...", "")):
        cmd = cmd.replace(placeholder, value)
    return cmd.strip()


def words(text):
    return " ".join(text.split())


class OpenForReviewNeedsNoApprovalTest(unittest.TestCase):
    def open_commands(self):
        cmds = [concrete(c) for c in commands(read(OPEN))]
        self.assertTrue(any(c.startswith(f"python3 {PLUGIN}/scripts/agent-github-token.py run gh pr create") for c in cmds))
        self.assertTrue(any(c.startswith("gh pr edit 1408 --body-file") and "--attach" in c for c in cmds))
        return cmds

    def test_every_command_of_phase_one_is_no_release(self):
        for cmd in self.open_commands():
            self.assertEqual(gate.classify(cmd, "/work/endoxia", branch_of=lambda _d: "t3/feature-x",
                                           scope=SESSION), set(), cmd)
            self.assertIsNone(bash_decision(Transcript().prose("go"), cmd, branch="t3/feature-x", scope=SESSION,
                                            cwd="/work/endoxia"), cmd)

    def test_the_branch_push_and_the_pr_text_need_no_approval_bare_or_as_the_app(self):
        for cmd in ("git push -u origin t3/feature-x", "gh pr create --title x --body-file b.md",
                    "gh pr edit 1408 --body-file b.md --attach ./a.mp4", "gh pr view t3/feature-x --json number,state,url",
                    f"python3 {PLUGIN}/scripts/agent-github-token.py run git push -u origin t3/feature-x",
                    f"python3 {PLUGIN}/scripts/agent-github-token.py run gh pr create --title x --body-file b.md",
                    f"python3 {PLUGIN}/scripts/agent-github-token.py run gh pr edit 1408 --body-file b.md"):
            self.assertIsNone(bash_decision(Transcript().prose("go"), cmd, branch="t3/feature-x", scope=SESSION,
                                            cwd="/work/endoxia"), cmd)

    def test_phase_one_spends_no_open_approval(self):
        t = Transcript().typed("approve", "ship")
        for cmd in self.open_commands():
            t.bash(cmd, branch="t3/feature-x", cwd="/work/endoxia")
        self.assertIsNone(bash_decision(t, "gh pr merge 1408 --squash --match-head-commit " + "a" * 40,
                                        branch="t3/feature-x", scope=SESSION, cwd="/work/endoxia"))

    def test_a_release_run_through_the_app_wrapper_is_refused_even_when_approved(self):
        for cmd, approve in ((f"python3 {PLUGIN}/scripts/agent-github-token.py run gh pr merge 1408 --squash", "ship"),
                             (f"python3 {PLUGIN}/scripts/agent-github-token.py run git push origin main", "quickship"),
                             (f"python3 {PLUGIN}/scripts/agent-github-token.py run git push origin deploy-1", "deploy")):
            reason = bash_decision(Transcript().typed("approve", approve), cmd, branch="t3/feature-x", scope=SESSION,
                                   cwd="/work/endoxia")
            self.assertIn("run through agent-github-token.py", reason or "", cmd)

    def test_the_ship_skill_itself_still_needs_an_approval(self):
        self.assertIsNotNone(skill_decision(Transcript().prose("go"), "claude-tdd-workflow:ship"))


class LandsExactlyTheReviewedHeadTest(unittest.TestCase):
    def test_land_checks_the_pr_and_its_evidence_before_merging_that_head(self):
        text = skill("ship")
        land = text[text.index("## Steps (land)"):text.index("## Integrate main")]
        covers = "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py review-covers <PR>"
        self.assertIn(covers, commands(land))
        self.assertLess(land.index(covers), land.index("gh pr merge <PR> --squash --match-head-commit <SHA>"))
        self.assertLess(land.index("gh pr view --json number,state,headRefOid"), land.index(covers))
        self.assertIn("Never land a head the owner has not been shown.", words(land))
        self.assertIn("open-for-review.md", land)
        self.assertIn("STOP", land[land.index(covers):land.index("4. **Wait")])

    def test_after_a_github_approval_a_new_head_needs_the_owner_again(self):
        land = words(skill("ship"))
        self.assertIn("so an approval given as a GitHub review no longer covers it", land)

    def test_the_approval_names_all_three_forms_and_is_spent_by_the_merge(self):
        text = words(skill("ship"))
        for phrase in ("`/approve ship`", "approving review on GitHub", "spent by the merge taking effect",
                       "ONE staging deploy tag on exactly the merge commit"):
            self.assertIn(phrase, text)

    def test_a_wake_reads_the_review_and_lands_or_works_the_changes(self):
        text = skill("ship")
        self.assertIn("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_app.py review <PR>", commands(text))
        self.assertIn("Do not merge on a wake alone", text)
        self.assertIn("watch_pull_request", read(OPEN))
        self.assertIn("link_pull_request", read(OPEN))
        self.assertIn("Without the watch tool the owner types `/ship`", words(read(OPEN)))

    def test_the_deploy_is_watched_in_the_turn_and_a_red_one_stays_with_the_owner(self):
        text = words(skill("ship"))
        self.assertIn("The turn does not end before the deploy run's verdict.", text)
        report = text[text.index("8. **Report"):text.index("## Integrate main")]
        self.assertLess(report.index("**Green**"), report.index("`settle`"))
        red = report[report.index("**Red"):]
        for phrase in ("STARTS with that", "do not settle", "`pin`", "`mark_unread`", "pnpm release:status"):
            self.assertIn(phrase, red)

    def test_verify_opens_the_pr_at_ready_instead_of_suggesting_ship(self):
        text = words(read(VERIFY))
        self.assertNotIn("Suggest `/ship`", text)
        after = text[text.index("## After the verdict"):]
        self.assertLess(after.index("verify-state.py record`"), after.index("skills/ship/open-for-review.md"))
        self.assertIn("without asking", after)
        self.assertIn("it needs no approval", after)
        refactor = words(read(os.path.join(ROOT, "skills", "refactor", "phases", "05-verify.md")))
        self.assertIn("open-for-review.md", refactor)

    def test_phase_one_checks_evidence_and_writes_the_marker_first(self):
        text = read(OPEN)
        cmds = commands(text)
        for cmd in ("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py evidence",
                    "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py review-marker"):
            self.assertIn(cmd, cmds)
        self.assertIn("- NOT RUN: <criterion> (<reason>)", text)
        self.assertIn("Runtime evidence: out of scope (<outOfScope>)", text)
        self.assertLess(text.index("verify-state.py evidence"), text.index("run gh pr create"))
        self.assertNotIn("--attach", " ".join(c for c in cmds if "agent-github-token.py" in c),
                         "gh uploads attachments only with a user token")

    def test_version(self):
        manifest = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(manifest["version"], "1.10.0")


class AppSetupDocTest(unittest.TestCase):
    def test_the_setup_names_each_permission_and_why(self):
        text = words(read(os.path.join(ROOT, "docs", "APP-SETUP.md")))
        for phrase in ("Contents", "Pull requests", "Metadata", "Workflows", "Only on this account",
                       "Only select repositories", "chmod 600", "x-access-token", "github-app.json",
                       "generating-a-json-web-token-jwt-for-a-github-app", "botLogin", "ownerLogin"):
            self.assertIn(phrase, text)
        self.assertRegex(text, r"(?i)installation access token .*trigger")


if __name__ == "__main__":
    unittest.main()
