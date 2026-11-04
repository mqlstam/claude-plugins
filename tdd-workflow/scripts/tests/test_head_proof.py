"""A PR head carries exactly one pre-queue proof (owner, 2026-11-05).

On 2026-11-04 a head was proved by VERIFY's ship:check, by ship:check again after every
re-integration of main (most after an index-only conflict), by a CI PR proof on the same
head, and by the merge queue. The queue caught both Linux-only defects; the re-runs
caught neither. The rule lives once in skills/ship/head-proof.md; every skill that
validates a head or integrates main points at it, and through the merge queue a clean
or conflict-only integration runs no new full proof.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HEAD_PROOF = "${CLAUDE_PLUGIN_ROOT}/skills/ship/head-proof.md"


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    return re.sub(r"\s+", " ", text)


def section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class HeadProofRuleTest(unittest.TestCase):
    def test_the_rule_states_one_proof_never_both_and_the_queue_proves_the_merge(self):
        text = flat(read("skills/ship/head-proof.md"))
        self.assertIn("Exactly one proof per head", text)
        self.assertIn("Never both on one head", text)
        self.assertIn("instead", text)
        self.assertIn(".claude/rules/verify-phase.md", text, "the project names where it runs")
        self.assertIn("runs **no new full proof**", text)
        self.assertIn("conflict-only", text)
        self.assertIn("proves that merge result before main moves", text)
        self.assertIn("merge-queue.py mode", text)
        # landing directly still validates the merged tree: nothing else proves it there
        directly = section(text, "**Directly**", "`2`")
        self.assertIn("validated once after the integration", directly)

    def test_verify_runs_the_one_proof_and_again_only_after_a_fix(self):
        text = read("skills/feature/phases/04-verify.md")
        step_c = flat(section(text, "**c. Local validation**", "Do not edit the tree"))
        self.assertIn(HEAD_PROOF, step_c)
        self.assertIn("never both on one head", step_c)
        step_4 = flat(section(text, "## Step 4", "## Step 5"))
        self.assertIn("the head's one proof runs again on the final tree", step_4)

    def test_every_integration_of_main_defers_to_the_rule(self):
        for relative, start, end in (
            ("skills/ship/open-for-review.md", "3. **Integrate main**", "4. **Push the branch"),
            ("skills/ship/SKILL.md", "**Local validation.**", "Before every push"),
            ("skills/merge/SKILL.md", "Read this checkout's `package.json` after merging",
             "Before every push"),
            ("skills/quickship/SKILL.md", "5. **Validate the merged tree**", "6. **Report main health."),
        ):
            step = flat(section(read(relative), start, end))
            self.assertIn(HEAD_PROOF, step, relative)

    def test_through_the_queue_a_clean_or_conflict_only_integration_runs_no_proof(self):
        for relative, start, end in (
            ("skills/ship/open-for-review.md", "3. **Integrate main**", "4. **Push the branch"),
            ("skills/ship/SKILL.md", "**Local validation.**", "Before every push"),
            ("skills/merge/SKILL.md", "Read this checkout's `package.json` after merging",
             "Before every push"),
        ):
            step = flat(section(read(relative), start, end))
            self.assertIn("Through the merge queue", step, relative)
            self.assertIn("no new full proof", step, relative)

    def test_open_for_review_no_longer_validates_the_merged_tree_unconditionally(self):
        step = flat(section(read("skills/ship/open-for-review.md"), "3. **Integrate main**",
                            "4. **Push the branch"))
        self.assertNotIn("then local validation on the merged tree", step)
        # it names no check of its own: what runs is head-proof.md's
        self.assertNotIn("ship:check", step)

    def test_after_an_integration_the_check_command_is_reached_only_landing_directly(self):
        # A second full proof restored in the queue path turns this red: the
        # command may appear only after the direct-landing branch of the step.
        for relative, start, end in (
            ("skills/ship/SKILL.md", "**Local validation.**", "Before every push"),
            ("skills/merge/SKILL.md", "Read this checkout's `package.json` after merging",
             "Before every push"),
            ("skills/quickship/SKILL.md", "5. **Validate the merged tree**", "6. **Report main health."),
        ):
            step = flat(section(read(relative), start, end))
            queue, _, direct = step.partition("**Directly**" if "**Directly**" in step else "Landing directly")
            self.assertTrue(direct, relative)
            self.assertNotIn("pnpm ship:check", queue, relative)
            self.assertNotIn("generic.md", queue, relative)
            self.assertIn("pnpm ship:check", direct, relative)

    def test_verify_runs_no_ship_check_when_the_ci_proof_is_chosen(self):
        step_c = flat(section(read("skills/feature/phases/04-verify.md"), "**c. Local validation**",
                              "Do not edit the tree"))
        self.assertIn("When the rule picks the CI PR proof for this change, dispatch that and run no `ship:check`",
                      step_c)
        self.assertLess(step_c.index("run no `ship:check`"), step_c.index("run `pnpm ship:check`"))

    def test_the_methodology_summary_runs_no_second_check_at_ready(self):
        text = flat(read("skills/tdd-methodology/SKILL.md"))
        summary = text.split("## Local checks and release gates", 1)[1].split("Each worktree runs", 1)[0]
        self.assertIn("head-proof.md", summary)
        self.assertNotIn("on the integrated tree", summary)


if __name__ == "__main__":
    unittest.main()
