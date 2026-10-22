"""Owner, 2026-10-21: agents verify in the harness's own browser and VERIFY keeps
screenshots and recordings as evidence that a later step attaches to the PR."""
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as fh:
        return fh.read()


def words(text):
    """The text with every run of whitespace as one space, so rewrapping is not a failure."""
    return " ".join(text.split())


VERIFY = "skills/feature/phases/04-verify.md"
VERIFIER = "agents/runtime-verifier.md"


class HarnessBrowserFirstTest(unittest.TestCase):
    def test_verifier_prefers_the_harness_browser_on_a_private_profile_and_own_tab(self):
        text = read(VERIFIER)
        browser = text.index("## The browser")
        self.assertLess(text.index("built-in browser", browser), text.index("Chrome DevTools MCP", browser))
        for phrase in ("preview_open", "reuseExistingTab: false", "tabId", "incognito",
                       "Never use the default profile"):
            self.assertIn(phrase, words(text), phrase)

    def test_wiring_agent_may_use_the_harness_browser(self):
        text = read("agents/wiring-agent.md")
        tools = text.split("\n---", 1)[0]
        self.assertIn("mcp__t3-code__preview_snapshot", tools)
        self.assertIn("mcp__chrome-devtools__navigate_page", tools)
        self.assertLess(text.index("preview_*"), text.index("Otherwise, Chrome DevTools MCP"))

    def test_spinup_starts_no_browser_inside_a_harness_that_has_one(self):
        text = read("skills/spinup/SKILL.md")
        self.assertNotIn("headless Chrome)", text.split("\n---", 1)[0])
        step = text[text.index("5. **The browser.**"):text.index("6. **Start the host BFF")]
        self.assertLess(step.index("nothing is started"), step.index("./scripts/worktree-chrome.sh"))

    def test_plugin_text_stays_project_agnostic(self):
        for relative in (VERIFY, VERIFIER, "agents/wiring-agent.md"):
            text = read(relative)
            for project_word in ("Endoxia", "staging.endoxia", "pnpm staging:access"):
                self.assertNotIn(project_word, text, relative)


class EvidenceTest(unittest.TestCase):
    def test_verifier_saves_a_screenshot_per_criterion_and_a_short_recording_per_flow(self):
        text = read(VERIFIER)
        for phrase in (".claude/verify-evidence/r<N>/", "git check-ignore", "save: true",
                       "screenshotPath", "preview_recording_start", "preview_recording_stop",
                       "a few seconds to about a minute"):
            self.assertIn(phrase, words(text), phrase)
        report = text[text.index("RUNTIME VERIFICATION"):]
        self.assertIn("<path in the checkout> | <criterion> | <one-sentence caption", report)
        # The existing contract stays: NOT RUN is never a pass, freshness is checked.
        self.assertIn("NOT RUN", report)
        self.assertIn("never VERIFIED", text)

    def test_verify_empties_the_directory_hands_the_round_on_and_writes_the_manifest_before_record(self):
        text = words(read(VERIFY))
        self.assertIn("**Empty the evidence directory** in every VERIFY", text)
        self.assertIn("evidence directory (`.claude/verify-evidence/r1/`)", text)
        self.assertLess(text.index("write the evidence manifest"), text.index("verify-state.py record`"))
        self.assertIn("NOT RUN is never a pass", text)

    def test_manifest_example_is_valid_json_with_the_documented_fields(self):
        text = read(VERIFY)
        section = text[text.index("### The evidence manifest"):]
        self.assertIn(".claude/verify-evidence/manifest.json", section)
        block = re.search(r"```json\n(.*?)```", section, re.S).group(1)
        manifest = json.loads(block)
        # Owner, 2026-10-22: every change writes one; an observable change names its
        # user flows, and each recording the flow it shows.
        self.assertEqual(set(manifest), {"verdict", "recordedAt", "slice", "observable", "outOfScope", "flows",
                                         "items", "notRun"})
        kinds = {item["kind"] for item in manifest["items"]}
        self.assertEqual(kinds, {"screenshot", "recording"})
        for item in manifest["items"]:
            extra = {"flow"} if item["kind"] == "recording" else set()
            self.assertEqual(set(item), {"path", "kind", "criterion", "caption"} | extra)
            self.assertTrue(item["path"].startswith(".claude/verify-evidence/r"))
        self.assertEqual(set(manifest["notRun"][0]), {"criterion", "reason"})
        recorded = {item.get("flow") for item in manifest["items"]} | {e["criterion"] for e in manifest["notRun"]}
        self.assertLessEqual(set(manifest["flows"]), recorded, "each flow has a recording or a reason")

    def test_recording_every_user_flow_is_required_for_an_observable_change(self):
        # The causes of 10 of 15 user-visible PRs without a recording (2026-10-22):
        # recordings only "when the criterion is a flow", READY with runtime "skipped",
        # and a manifest only when runtime verification was in scope.
        verifier = words(read(VERIFIER))
        self.assertNotIn("when the criterion is a flow rather than a state", verifier)
        self.assertIn("One short recording per user flow, always.", verifier)
        verify = words(read(VERIFY))
        self.assertNotIn("or was skipped or NOT RUN", verify)
        self.assertNotIn("when runtime verification was in scope", verify)
        self.assertIn("Hard to provoke, or covered by tests, is not out of scope.", verify)
        self.assertIn("write the evidence manifest (below), for every change", verify)
        self.assertLess(verify.index("verify-state.py evidence`"), verify.index("verify-state.py record`"))


if __name__ == "__main__":
    unittest.main()
