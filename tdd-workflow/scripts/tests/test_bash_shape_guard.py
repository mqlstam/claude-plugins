"""Tests for bash-shape-guard.py. Run: python3 -I -m unittest discover -s scripts/tests"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bash-shape-guard.py")
SPEC = importlib.util.spec_from_file_location("guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def decide(command, background=False, **extra):
    tool_input = {"command": command}
    if background:
        tool_input["run_in_background"] = True
    return guard.decide({"tool_name": "Bash", "tool_input": tool_input, **extra})


class ForegroundWaitTest(unittest.TestCase):
    # Every refused command below is a shape measured in the 2026-09-01..10-12 transcripts.
    def test_a_long_sleep_before_reading_a_task_output_is_refused(self):
        reason = decide("sleep 190; cat /private/tmp/claude-501/x/tasks/btowt8ytv.output")
        self.assertIn("190 s", reason)
        self.assertIn("run_in_background", reason)

    def test_sleeps_add_up(self):
        self.assertIsNotNone(decide("sleep 6; gh pr view 1 --json state; sleep 6; gh pr view 1 --json state"))

    def test_units_and_infinity(self):
        self.assertIsNotNone(decide("sleep 1m && tail -3 /tmp/x.log"))
        self.assertIsNotNone(decide("sleep infinity"))
        self.assertIsNone(decide("sleep 0.5; ls"))

    def test_a_settle_pause_after_a_signal_is_allowed(self):
        self.assertIsNone(decide("gh run cancel 37217765963 && sleep 5 && gh run view 37217765963 --json status"))
        self.assertIsNone(decide("kill 123; sleep 1; ps -p 123"))
        self.assertIsNone(decide(f"sleep {guard.SETTLE_BOUND_S:g}; docker ps"))

    def test_the_bound_is_exclusive_above(self):
        self.assertIsNotNone(decide(f"sleep {guard.SETTLE_BOUND_S + 1:g}; docker ps"))

    def test_any_sleep_in_a_loop_is_refused(self):
        self.assertIn("loop", decide('until grep -q "^exit" /tmp/b.log; do sleep 15; done; tail -12 /tmp/b.log'))
        self.assertIn("loop", decide("for i in $(seq 1 30); do curl -s localhost:3000 && break; sleep 1; done"))
        self.assertIn("loop", decide("while true\ndo\n  gh run view 1 --json status\n  sleep 2\ndone"))

    def test_a_loop_handed_to_a_shell_is_still_a_loop(self):
        cmd = "timeout 3600 bash -c 'while ! grep -q LANE2_EXIT= /tmp/lane2.out; do sleep 20; done'"
        self.assertIn("loop", decide(cmd))
        self.assertIsNotNone(decide("ssh -n host 'a=$(docker inspect -f x c); sleep 60; b=$(docker inspect -f x c)'"))
        self.assertIsNotNone(decide("ssh host 'bash -s' <<'EOF'\nfor i in 1 2 3; do sleep 2; done\nEOF"))

    def test_sleep_in_text_is_not_a_command(self):
        self.assertIsNone(decide('git commit -m "fix: wait less (was sleep 300 in a loop)"'))
        self.assertIsNone(decide("echo 'for i in 1 2; do sleep 60; done'"))
        self.assertIsNone(decide("python3 - <<'PY'\nimport time\nfor _ in range(9):\n    time.sleep(60)\nPY"))
        self.assertIsNone(decide("grep -rn 'sleep 300' scripts/ # sleep 300"))
        self.assertIsNone(decide("cat > /tmp/x.sh <<'EOF'\nwhile true; do sleep 60; done\nEOF"))
        self.assertIsNone(decide("pmset -g | grep sleep"))

    def test_a_computed_duration_is_not_refused_outside_a_loop(self):
        self.assertIsNone(decide('sleep "$DELAY"; ls'))

    def test_watches_are_refused_in_the_foreground(self):
        self.assertIn("gh run watch", decide("gh run watch 123 || true"))
        self.assertIn("--watch", decide("gh pr checks 171 --watch --interval 15"))
        self.assertIn("watch", decide("watch -n 5 docker ps"))
        self.assertIsNone(decide("gh pr checks 171"))
        self.assertIsNone(decide("pnpm exec vitest --watch=false run"))

    def test_background_is_the_sanctioned_shape(self):
        for cmd in ("sleep 190; cat /tmp/x", "until test -f /tmp/done; do sleep 5; done", "gh run watch 1"):
            self.assertIsNone(decide(cmd, background=True), cmd)

    def test_applies_inside_subagents_too(self):
        self.assertIsNotNone(decide("sleep 240; echo woke", agent_id="agent-1"))


class MaskedStatusTest(unittest.TestCase):
    def test_a_filters_status_used_as_the_producers_is_refused(self):
        # 2026-09-22: the build failed, `tail` exited 0, `up -d` rolled the old image.
        reason = decide("docker compose build core 2>&1 | tail -4 && docker compose up -d core")
        self.assertIn("pipeline's status", reason)
        self.assertIn("`&&`", reason)
        self.assertIn("`$?`", decide('pnpm exec tsc --noEmit -p . 2>&1 | tail -20; echo "EXIT=$?"'))
        self.assertIn("`$?`", decide("pnpm -s check:markers | tail -5\necho exit=$?"))
        self.assertIn("`if`", decide("if pnpm build 2>&1 | tail -3; then echo ok; fi"))
        self.assertIn("`||`", decide("pnpm install --offline 2>&1 | tail -5 || pnpm install"))
        self.assertIn("`&&`", decide("pnpm format:check |& tail -2 && node scripts/check-summary.mjs format:check"))

    def test_reading_through_a_pipe_is_fine(self):
        self.assertIsNone(decide("pnpm test 2>&1 | tail -20"))
        self.assertIsNone(decide("grep -n foo a.ts | head -5 && sed -n 1,5p b.ts"))
        self.assertIsNone(decide("git log --oneline | head -3; git status --short"))
        self.assertIsNone(decide("pnpm build 2>&1 | tail -3 || true"))
        self.assertIsNone(decide("pnpm test 2>&1 | grep -q FAIL && echo failed"))
        self.assertIsNone(decide('pnpm build 2>&1 | tail -3; echo "${PIPESTATUS[0]}"'))

    def test_pipefail_makes_the_status_honest(self):
        self.assertIsNone(decide("set -euo pipefail\ndocker compose build core 2>&1 | tail -4 && docker compose up -d"))
        self.assertIsNone(decide("setopt pipefail; pnpm build 2>&1 | tail -3; echo $?"))

    def test_quoted_pipes_and_operators_are_not_a_pipeline(self):
        self.assertIsNone(decide('git commit -m "a | tail && b"'))
        self.assertIsNone(decide("gh pr view 1 --json state -q '.state | ascii_downcase' && echo ok"))

    def test_applies_in_the_background_too(self):
        self.assertIsNotNone(decide("pnpm build 2>&1 | tail -3 && pnpm test", background=True))


class EntryPointTest(unittest.TestCase):
    def run_hook(self, payload):
        return subprocess.run([sys.executable, "-I", SCRIPT], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)

    def test_denies_with_the_hook_shape(self):
        out = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "sleep 300"}})
        self.assertEqual(out.returncode, 0)
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertEqual(decision["hookEventName"], "PreToolUse")

    def test_allows_silently(self):
        out = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_a_crash_allows(self):
        out = self.run_hook({"tool_name": "Bash", "tool_input": None})
        self.assertEqual((out.returncode, out.stdout), (0, ""))
        out = subprocess.run([sys.executable, "-I", SCRIPT], input="not json", capture_output=True,
                             text=True, timeout=30)
        self.assertEqual((out.returncode, out.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
