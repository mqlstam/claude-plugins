"""Tests for inject-context.py and its wiring in hooks.json."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "scripts", "inject-context.py")


def run(payload, env_root=ROOT):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": env_root}
    return subprocess.run([sys.executable, "-I", SCRIPT], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env)


class InjectContextTest(unittest.TestCase):
    def test_subagents_and_sessions_get_the_whole_text(self):
        with open(os.path.join(ROOT, "context", "bash-command-shape.md"), encoding="utf-8") as fh:
            text = fh.read().strip()
        for event in ("SubagentStart", "SessionStart"):
            out = run({"hook_event_name": event, "agent_type": "general-purpose"})
            self.assertEqual(out.returncode, 0)
            body = json.loads(out.stdout)["hookSpecificOutput"]
            self.assertEqual(body["hookEventName"], event)
            self.assertEqual(body["additionalContext"], text)

    def test_the_text_fits_without_being_cut_to_a_preview(self):
        with open(os.path.join(ROOT, "context", "bash-command-shape.md"), encoding="utf-8") as fh:
            self.assertLess(len(fh.read().strip()), 10_000)

    def test_the_text_names_the_measured_shapes(self):
        with open(os.path.join(ROOT, "context", "bash-command-shape.md"), encoding="utf-8") as fh:
            text = fh.read()
        for needle in ("heredoc", "zsh", "${ACCT}:role", "last command's exit status",
                       "run_in_background", "absolute paths"):
            self.assertIn(needle, text)

    def test_other_events_and_a_missing_file_print_nothing(self):
        self.assertEqual(run({"hook_event_name": "PreToolUse"}).stdout, "")
        out = run({"hook_event_name": "SubagentStart"}, env_root="/nonexistent")
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_hooks_json_wires_both_events_and_the_bash_guard(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]
        for event in ("SubagentStart", "SessionStart"):
            commands = [h["command"] for entry in hooks[event] for h in entry["hooks"]]
            self.assertTrue(any("inject-context.py" in c for c in commands), event)
        bash_commands = [h["command"] for entry in hooks["PreToolUse"]
                         if "Bash" in entry.get("matcher", "") for h in entry["hooks"]]
        self.assertTrue(any("bash-shape-guard.py" in c for c in bash_commands))
        for entry in hooks["SubagentStart"]:
            self.assertNotIn("matcher", entry, "every subagent type gets the text")


if __name__ == "__main__":
    unittest.main()
