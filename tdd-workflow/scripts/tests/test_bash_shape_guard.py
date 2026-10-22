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


class ReleaseWaitTest(unittest.TestCase):
    # The command of 2026-10-20 22:23:11Z: started in the background, killed 7 s later
    # with its session, and the run's failure went unseen until 08:12Z.
    INCIDENT = ("python3 /Users/miquelstam/.claude_ai2/plugins/cache/mqlstam-plugins/claude-tdd-workflow/"
                "1.8.0/scripts/release-run.py await 39507708450 --deadline-min 305")
    SKILL = "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py await 123 --deadline-min 190 --slice-min 9"

    def test_a_background_await_is_refused(self):
        # Mutation: without the background branch, the incident's command runs.
        reason = decide(self.INCIDENT, background=True)
        self.assertIn("in the background", reason)
        self.assertIn("--slice-min 9", reason)
        self.assertIn("Nothing ran", reason)
        self.assertIsNotNone(decide(self.SKILL, background=True), "a slice does not make it safe")
        quoted = 'python3 -I "/Users/x/.claude/plugins/cache/p/1.8.2/scripts/release-run.py" await 7 --slice-min 9'
        self.assertIsNotNone(decide(quoted, background=True))

    def test_the_slice_it_names_is_the_scripts(self):
        spec = importlib.util.spec_from_file_location("release_run", os.path.join(os.path.dirname(HERE),
                                                                                  "release-run.py"))
        release_run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release_run)
        self.assertIn(f"`--slice-min {release_run.FOREGROUND_SLICE_MIN}`", decide(self.INCIDENT))

    def test_a_foreground_await_needs_a_slice(self):
        # Mutation: without the slice check, a foreground await waits past the Bash tool.
        self.assertIn("--slice-min", decide(self.INCIDENT))
        self.assertIsNone(decide(self.SKILL))
        self.assertIsNone(decide(self.SKILL.replace("--slice-min 9", "--slice-min=9")))

    def test_the_rule_does_not_hide_behind_the_other_rules(self):
        # Mutation: checked after rule 1's early exit (no sleep, watch or pipe), it never runs.
        self.assertIsNotNone(decide("python3 release-run.py await 1 --deadline-min 5"))

    # Review finding (Codex, 2026-10-21): valid shell spellings of the same await.
    AWAIT = "python3 /p/scripts/release-run.py await 7 --deadline-min 190 --slice-min 9"
    VERDICT = "a release run's verdict in the background"

    def test_quoting_inside_a_word_does_not_hide_the_script(self):
        # Mutation: matching the raw text instead of the unquoted words lets these run.
        for cmd in ('python3 /p/scripts/release-"run".py await 7 --deadline-min 190 --slice-min 9',
                    "python3 /p/scripts/release\\-run.py await 7 --deadline-min 190 --slice-min 9",
                    "python3 /p/scripts/'release-run.py' 'await' 7 --slice-min 9 2>&1 > /tmp/a.log"):
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)

    def test_the_review_spellings_without_a_slice_are_refused(self):
        # Review finding (Codex, 2026-10-22), reproduced on the installed 1.8.2 guard,
        # which matched the raw substring `release-run.py` and read no `-c` string.
        # Mutation: either of those readings lets both run in the background.
        for cmd in ('python3 "/p/scripts/release-"run.py await 1 --deadline-min 190',
                    "bash -c 'python3 /p/scripts/release-run.py await 1 --deadline-min 190'"):
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)
            self.assertIn("has no `--slice-min`", decide(cmd) or "", cmd)

    def test_a_shell_string_is_read_as_a_call_of_its_own(self):
        # Mutation: not reading a shell's `-c` string lets these run.
        for cmd in (f"bash -c '{self.AWAIT}'", f'bash -lc "{self.AWAIT}"',
                    f"bash scripts/wt.sh sh -c '{self.AWAIT}'", f"timeout 600 zsh -c '{self.AWAIT}'",
                    f"""bash -c "sh -c '{self.AWAIT}'" """):
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)
        no_slice = self.AWAIT.replace(" --slice-min 9", "")
        self.assertIn("--slice-min", decide(f"bash -c '{no_slice}'") or "")

    def test_a_heredoc_a_shell_reads_is_read_as_a_call_of_its_own(self):
        # Mutation: not reading a shell's heredoc lets these run.
        for cmd in (f"bash <<'EOF'\n{self.AWAIT}\nEOF", f"bash -s -- 7 <<EOF\necho start\n{self.AWAIT}\nEOF"):
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)
        no_slice = self.AWAIT.replace(" --slice-min 9", "")
        self.assertIn("--slice-min", decide(f"bash <<'EOF'\n{no_slice}\nEOF") or "")

    def test_a_shell_fed_from_a_pipe_is_refused_in_the_background(self):
        # Mutation: reading only the shell's own words lets the piped await run unseen.
        piped = f"echo '{self.AWAIT}' | bash"
        self.assertIn(self.VERDICT, decide(piped, background=True) or "")
        self.assertIsNone(decide(piped))
        self.assertIsNone(decide("echo 'ls -la' | bash", background=True))

    def test_the_command_word_is_found_behind_wrappers(self):
        # Owner, 2026-10-21: an await is the program in command position (behind
        # assignments, env/nohup/timeout/xargs, or a shell running a wrapper script such
        # as scripts/wt.sh), or the script an interpreter there runs. Mutation: without
        # the wrapper walk, the foreground ones without a slice run.
        for cmd in (f"env PYTHONUNBUFFERED=1 nohup {self.AWAIT.replace('python3', 'python3 -u -X utf8')}",
                    f"bash scripts/wt.sh {self.AWAIT}",
                    f"echo 7 | xargs -I{{}} {self.AWAIT.replace(' 7 ', ' {} ')}",
                    "/p/scripts/release-run.py await 7 --deadline-min 190 --slice-min 9",
                    f"command exec timeout 600 {self.AWAIT}"):
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)
            self.assertIsNone(decide(cmd), cmd)
            self.assertIn("has no `--slice-min`", decide(cmd.replace(" --slice-min 9", "")) or "", cmd)

    # Review round 2 (Codex, 2026-10-22): shell constructs hid an await from the word
    # reader, and a lone `&` made a foreground call return at once. The shape, not the
    # spelling: a command whose de-quoted text names release-run and `await` anywhere is
    # refused in the background and with any job-control operator.
    BYPASSES = (f'result="$({AWAIT})"', f"x=`{AWAIT}`", f"if true; then {AWAIT}; fi",
                f"while true; do {AWAIT}; done", f"{{ {AWAIT}; }}", f"! {AWAIT}",
                f"env X=1 {AWAIT}", f"exec {AWAIT}", f"eval '{AWAIT}'", f"nohup {AWAIT}",
                'R=/p/scripts/release-run.py; python3 "$R" await 7 --deadline-min 190 --slice-min 9',
                "python3 -m release_run await 7 --deadline-min 190 --slice-min 9",
                f"setsid {AWAIT}", f"( {AWAIT} )", f"echo 'release-run.py await' > /tmp/n.md")

    def test_any_named_await_is_refused_in_the_background(self):
        # Mutation: reading only command words lets the constructs run in the background.
        for cmd in self.BYPASSES:
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)

    def test_a_job_control_operator_is_refused_in_the_foreground(self):
        # Mutation: deciding background on the tool flag alone lets these return at once.
        jobs = (f"{self.AWAIT} >log 2>&1 &", f"nohup {self.AWAIT} &", f"( {self.AWAIT} ) &",
                f"{self.AWAIT} & disown", f"setsid {self.AWAIT}", f"bash -c '{self.AWAIT} &'",
                f"coproc {self.AWAIT}", f"{self.AWAIT} & wait", f"setsid -f {self.AWAIT}")
        for cmd in jobs:
            self.assertIn("as a background job", decide(cmd) or "", cmd)
        for cmd in (f"{self.AWAIT} > /tmp/a.log 2>&1; echo exit=$?", f"{self.AWAIT} &>/tmp/a.log",
                    f"{self.AWAIT} && echo done", f"cd /p && {self.AWAIT}"):
            self.assertIsNone(decide(cmd), cmd)
        self.assertIsNone(decide("sleep 1 & wait"), "no release-run named: not this rule")

    def test_constructs_do_not_hide_a_missing_slice_in_the_foreground(self):
        # Mutation: not reading substitutions, control-flow bodies or eval's words lets
        # these wait past the Bash tool's 10 minutes.
        for cmd in self.BYPASSES[:10]:
            no_slice = cmd.replace(" --slice-min 9", "")
            self.assertIn("has no `--slice-min`", decide(no_slice) or "", no_slice)
            self.assertIsNone(decide(cmd), cmd)

    def test_printed_or_searched_text_is_no_command_in_the_foreground(self):
        # A shell named among another program's arguments, the script's name among
        # grep's, a commit message or a heredoc written to a file runs nothing, so it
        # passes in the foreground. In the background it is refused: the guard fails
        # closed there, and the mention is written in a foreground call instead.
        mentions = (f"echo bash -c '{self.AWAIT}'", f"printf '%s\\n' bash -c '{self.AWAIT}'",
                    "grep -n -e release-run.py await", "grep -rn release-run.py await skills/",
                    'git commit -m "docs: release-run.py await 1 runs in the foreground"',
                    'grep -n "release-run.py await" skills/deploy/SKILL.md',
                    "grep -rn 'release-\"run\".py await' scripts/",
                    "cat > /tmp/x.md <<'EOF'\npython3 release-run.py await 1\nEOF",
                    f"cat > /tmp/x.sh <<'EOF'\nbash -c '{self.AWAIT}'\nEOF",
                    f"git commit -m \"fix: bash -c '{self.AWAIT}' now refused\"")
        for cmd in mentions:
            self.assertIsNone(decide(cmd), cmd)
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)

    # Review round 3 (Codex, 2026-10-22).
    def test_a_detacher_counts_by_its_name_in_command_position(self):
        # Mutation: matching the detacher's name in the text misses an absolute path.
        for cmd in (f"/usr/bin/setsid -f {self.AWAIT}", f"env X=1 /usr/bin/setsid {self.AWAIT}",
                    f"command exec /bin/setsid {self.AWAIT}", f"nohup /opt/bin/daemonize {self.AWAIT}"):
            self.assertIn("as a background job", decide(cmd) or "", cmd)
        # A detacher named as another program's argument detaches nothing.
        for cmd in (f"echo setsid; {self.AWAIT}", f"grep -n disown notes.md && {self.AWAIT}"):
            self.assertIsNone(decide(cmd), cmd)

    def test_a_line_continuation_is_removed_as_bash_removes_it(self):
        # Bash drops a backslash-newline pair outside single quotes before it reads
        # words. Mutation: dropping the backslash alone leaves `aw\nait`, no `await`.
        for cmd in ("python3 /p/scripts/release-run.py aw\\\nait 7 --deadline-min 190 --slice-min 9",
                    "python3 /p/scripts/release-r\\\nun.py await 7 --deadline-min 190 --slice-min 9",
                    'bash -c "python3 /p/scripts/release-run.py aw\\\nait 7 --slice-min 9"'):
            self.assertIn(self.VERDICT, decide(cmd, background=True) or "", cmd)
        self.assertIn("has no `--slice-min`",
                      decide("python3 /p/scripts/release-run.py aw\\\nait 7 --deadline-min 190") or "")
        # Inside single quotes the pair is text: no await is named.
        self.assertIsNone(decide("echo 'release-run.py aw\\\nait'", background=True))

    def test_a_quoted_ampersand_is_no_job_control(self):
        # Mutation: reading `&` on the raw text refuses these.
        for cmd in (f"{self.AWAIT} > '/tmp/run&verdict.log' 2>&1", f"{self.AWAIT} > /tmp/run\\&v.log",
                    f'echo "build & verify"; {self.AWAIT}', f"{self.AWAIT} >&2", f"{self.AWAIT} |& cat > /tmp/a.log"):
            self.assertIsNone(decide(cmd), cmd)
        for cmd in (f"( {self.AWAIT} )&", f"{self.AWAIT}&", f"bash -c '{self.AWAIT} &'",
                    f'x="$({self.AWAIT} &)"', f"bash <<'EOF'\n{self.AWAIT} &\nEOF"):
            self.assertIn("as a background job", decide(cmd) or "", cmd)

    def test_other_release_run_commands_pass_in_either_mode(self):
        for cmd in ("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py find deploy.yml abc",
                    "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py reason 123"):
            self.assertIsNone(decide(cmd, background=True), cmd)
            self.assertIsNone(decide(cmd), cmd)


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

    def test_denies_a_background_release_await(self):
        out = self.run_hook({"tool_name": "Bash", "tool_input": {
            "command": ReleaseWaitTest.SKILL, "run_in_background": True, "timeout": 600000}})
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("in the background", decision["permissionDecisionReason"])

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
