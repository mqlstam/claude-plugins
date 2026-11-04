"""A builder carries on by itself when a long step it started ends (owner, 2026-11-04).

Delegated T3 builders ended their turn with "I'll report when it finishes" and sat until
the coordinator nudged them (the runner-fallback builder five times on 2026-11-04).
Measured that night: a `run_in_background` command wakes the conversation when it ends,
even after the turn ended, in delegated children and top-level threads alike, with or
without `t3_background_wait`. What wakes nobody is a wait on something outside the
machine (a GitHub run, a remote job) with no local task. The rule lives once in the
context every session and subagent gets at start; every skill step that waits in the
background says at the step that its completion wakes the model and the workflow goes on.
"""
import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONTEXT = os.path.join(ROOT, "context", "bash-command-shape.md")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def skill_files():
    return sorted(glob.glob(os.path.join(ROOT, "skills", "**", "*.md"), recursive=True))


def blocks(text):
    """Blank-line separated blocks; a numbered or bulleted step is one block."""
    return [b for b in re.split(r"\n\s*\n", text) if b.strip()]


def flat(text):
    return re.sub(r"\s+", " ", text)


BACKGROUND_STEP = re.compile(r"run_in_background|background (execution|calls)", re.I)


class OwnedBackgroundStepTest(unittest.TestCase):
    def test_the_context_states_the_rule_once_for_every_session(self):
        text = flat(read(CONTEXT))
        self.assertIn("A background step is yours until it ends", text)
        # what wakes the model, and what does not
        self.assertIn("wakes this conversation when it ends, even after the turn has ended", text)
        for outside in ("a GitHub run", "the merge queue", "a job on another host", "another thread"):
            self.assertIn(outside, text)
        # the external wait is wrapped in a local background command ending on the terminal state
        self.assertIn("local background command that polls it and exits on the terminal state", text)
        self.assertIn("gh run watch <id> --exit-status", text)
        # ending the turn on it is a defect unless handed back explicitly
        self.assertIn("is a defect", text)
        self.assertIn("hand the step back explicitly", text)
        # the release verdict stays a foreground wait, as its skills say
        self.assertIn("`release-run.py await` and `merge-queue.py await` run in foreground slices", text)

    def test_the_context_still_fits_the_hook_limit(self):
        spec_path = os.path.join(ROOT, "scripts", "inject-context.py")
        limit = int(re.search(r"ADDITIONAL_CONTEXT_LIMIT = ([\d_]+)", read(spec_path)).group(1).replace("_", ""))
        self.assertLess(len(read(CONTEXT).strip()), limit)

    def test_every_background_step_says_its_completion_wakes_you(self):
        missing = []
        for path in skill_files():
            for block in blocks(read(path)):
                if BACKGROUND_STEP.search(block) and "completion wakes you" not in flat(block):
                    missing.append(f"{os.path.relpath(path, ROOT)}: {block.strip()[:120]}")
        self.assertEqual(missing, [], "a background step that does not say it wakes you:\n" + "\n".join(missing))

    def test_the_build_skills_forbid_ending_the_turn_on_an_external_wait(self):
        for rel in ("skills/feature/SKILL.md", "skills/refactor/SKILL.md", "skills/feature/phases/04-verify.md"):
            text = flat(read(os.path.join(ROOT, rel)))
            self.assertRegex(text, r"(?i)never end(s)? (the|its) turn", rel)
            self.assertIn("local background command that exits on its terminal state", text, rel)


if __name__ == "__main__":
    unittest.main()
