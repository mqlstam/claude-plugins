"""Slice 826: direct authorized merges, local fast checks and staging health."""
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_release_approval_gate import FakeResolver, Transcript, gate, no_sleep, on_branch
from test_release_skills import ROOT, commands, read, skill

INTEGRATION_COMMAND = 'git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main'

# A test or typecheck run: a package manager, its options (`-r`, `--filter <pkg>`, ...)
# and `run`, then the script `test` or `typecheck`; or `turbo run test|typecheck`. Its
# words run to the end of the command (a backtick, `&&`, `;`, `|`, a line end) or of the
# sentence, so prose after a bare command ("pnpm test after every edit") is its words.
_RUNNER = re.compile(r"(?<![\w@/-])(?:pnpm|npm|yarn|turbo)(?![\w-])")
_COMMAND_END = re.compile(r"`|&&|;|\||\n|[.,:)](?=\s|$)")
# The package-manager options that take the next word as their value (pnpm(1)
# `--filter`/`-F`/`-C`/`--dir`, npm(1) `--workspace`/`-w`, yarn `--cwd`).
_VALUE_OPTIONS = {"--filter", "-F", "-C", "--dir", "--workspace", "--cwd"}
_SCRIPTS = {"test", "typecheck"}
# A repository-root vitest run and a Jest pattern over the whole suite (a root
# `vitest run <file>` matched no test in Endoxia and exited 0).
_ALWAYS_SUITE = re.compile(r"testPathPattern|pnpm exec vitest run")


def _names_a_test_file(word):
    """A test file (`a.test.ts`, `src/a.spec.tsx`, `src/__tests__/a.ts`) or the
    placeholder for one (`<file>`, `<files>`, `<test-file>`, `<file>…`). Any other
    path-like word, an option's value (`--coverage.reportsDirectory coverage/`) or a
    directory (`src/utils/`), selects nothing."""
    return bool(re.search(r"(?:^|/)[^/]+\.(?:test|spec)\.[A-Za-z0-9]+$", word) or "__tests__/" in word
                or re.fullmatch(r"<[^>]*file[^>]*>…?", word))


def suite_runs(text):
    """The test and typecheck runs in `text` that name no test file: a whole package or
    repository suite. `pnpm --filter <pkg> test` is the package's whole suite; `npm test
    -- <file>` runs one file. A typecheck has no per-file form, so it is always whole."""
    found = [m.group(0) for m in _ALWAYS_SUITE.finditer(text)]
    for m in _RUNNER.finditer(text):
        rest = text[m.end():]
        end = _COMMAND_END.search(rest)
        words = [m.group(0), *(rest[:end.start()] if end else rest).split()]
        i = 1
        if words[0] == "turbo":
            if words[1:2] != ["run"]:
                continue
            i = 2
        else:
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in _VALUE_OPTIONS or (words[0] == "npm" and words[i] == "-w") else 1
            if i < len(words) and words[i] == "run":
                i += 1
        if i >= len(words) or words[i] not in _SCRIPTS:
            continue
        args = [w for w in words[i + 1:] if not w.startswith("-")]
        if words[i] == "typecheck" or not any(_names_a_test_file(w) for w in args):
            found.append(" ".join(words[:i + 1]))
    return found


# The one exemption: the same sentence says the run needs a stated reason, in those
# words ("needs a stated reason", "only with a stated reason"), as CLAUDE.md's Build &
# Test does, and has no negation anywhere ("no validation command needs a stated
# reason", "Never skip it; it needs a stated reason to skip").
_STATED_REASON = re.compile(r"\b(?:needs|only with) a stated reason\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:no|not|never|without|none|nobody|nothing|neither|nor|cannot)\b|n't\b",
                       re.IGNORECASE)


def _sentences(text):
    for block in re.split(r"\n[ \t]*\n|\n(?=[ \t]*(?:[-*+]|\d+\.)[ \t])", text):
        yield from re.split(r"(?<=[.!?])\s+", " ".join(block.split()))


def unreasoned_suite_runs(text):
    """The whole-suite runs of a guidance text whose sentence does not say the run
    needs a stated reason (CLAUDE.md, Build & Test: "A whole package suite, `pnpm
    test`, `pnpm typecheck` or `turbo run test` needs a stated reason")."""
    found = []
    for sentence in _sentences(text):
        if _STATED_REASON.search(sentence) and not _NEGATION.search(sentence):
            continue
        found += suite_runs(sentence)
    return found


class DirectReleaseTest(unittest.TestCase):
    def test_local_integration_is_allowed_without_spending_release_authorization(self):
        from test_release_approval_gate import bash_decision
        transcript = Transcript().typed("approve", "merge")
        self.assertIsNone(bash_decision(transcript, INTEGRATION_COMMAND))
        transcript.bash(INTEGRATION_COMMAND)
        self.assertIsNone(bash_decision(
            transcript, "gh pr merge 42 --squash --match-head-commit abc"))
        # Adopting the branch's squash-merged PRs is local too, with or without approval.
        adopt = "python3 /p/claude-tdd-workflow/1.8.2/scripts/verify-state.py adopt-squash"
        self.assertIsNone(bash_decision(Transcript(), adopt))
        transcript = Transcript().typed("approve", "merge")
        transcript.bash(adopt)
        self.assertIsNone(bash_decision(
            transcript, "gh pr merge 42 --squash --match-head-commit abc"))

    def test_authorized_merge_needs_no_remote_main_policy(self):
        # The checkout may predate the new scripts. Authorization comes from the
        # user's transcript, never from origin/main's package.json or local refs.
        for typed, args in (("claude-tdd-workflow:ship", ""), ("approve", "merge")):
            transcript = Transcript().typed(typed, args)
            path = transcript.write()
            try:
                with patch.object(gate.subprocess, "run", side_effect=AssertionError("unexpected git/API probe")):
                    reason = gate.decide_bash(
                        {"tool_input": {"command": "gh pr merge 42 --squash --match-head-commit abc"},
                         "transcript_path": path, "cwd": "/repo"},
                        FakeResolver(), sleep=no_sleep, branch_of=on_branch("feature"),
                        scope=gate.GUARD_ALL, history_scope=gate.GUARD_ALL, world=gate.World())
                self.assertIsNone(reason)
            finally:
                os.unlink(path)

    def test_failed_direct_merge_preserves_approval_success_consumes_it(self):
        from test_release_approval_gate import bash_decision
        command = "gh pr merge 42 --squash --match-head-commit abc"
        transcript = Transcript().typed("approve", "merge")
        transcript.bash(command, error=True, output="Exit code 1\nhead changed")
        self.assertIsNone(bash_decision(transcript, command))
        transcript.bash(command)
        self.assertIsNotNone(bash_decision(transcript, command))

    def test_background_merge_holds_approval_until_a_trusted_failure(self):
        from test_release_approval_gate import FakeWorld, bash_decision
        command = "gh pr merge 42 --squash --match-head-commit abc"
        for status in (None, "completed"):
            transcript = Transcript().typed("approve", "merge")
            task = transcript.bash(command, background=True)
            if status:
                transcript.notify(task, status, 0)
            self.assertIsNotNone(bash_decision(transcript, command), status)
        # Ended without a verdict (killed, stopped, unknown): GitHub says whether it merged.
        for status in ("killed", "stopped", "unknown"):
            transcript = Transcript().typed("approve", "merge")
            task = transcript.bash(command, background=True)
            transcript.notify(task, status, 0)
            self.assertIsNotNone(bash_decision(transcript, command,
                                               world=FakeWorld(prs={"42": ("MERGED", "a" * 40)})), status)
            self.assertIsNone(bash_decision(transcript, command), status)
        transcript = Transcript().typed("approve", "merge")
        task = transcript.bash(command, background=True)
        transcript.notify("other-task", "failed", 1)
        transcript.notify(task, "failed", 1, sidechain=True)
        self.assertIsNotNone(bash_decision(transcript, command))
        transcript.notify(task, "failed", 1)
        self.assertIsNone(bash_decision(transcript, command))

    def test_refused_background_start_preserves_the_approval(self):
        from test_release_approval_gate import bash_decision
        command = "gh pr merge 42 --squash --match-head-commit abc"
        transcript = Transcript().typed("approve", "merge")
        transcript.bash(command, background=True, error=True, output="Blocked before running")
        self.assertIsNone(bash_decision(transcript, command))


class ReleaseRedesignContractTest(unittest.TestCase):
    def test_version_and_no_retired_mechanisms(self):
        manifest = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(manifest["version"], "1.10.0")
        for relative in ("skills/ship/SKILL.md", "skills/ship/generic.md", "skills/merge/SKILL.md", "skills/quickship/SKILL.md",
                         "skills/feature/phases/04-verify.md", "skills/refactor/phases/05-verify.md",
                         "skills/tdd-methodology/SKILL.md", "scripts/instance-manager.py",
                         "scripts/release-approval-gate.py"):
            text = read(os.path.join(ROOT, relative))
            for retired in ("ship:gate", "ship:land", "gate-lock", "gate-record", "land_queue", "land-queue", "FIFO"):
                self.assertNotIn(retired, text, relative)

    def test_ship_checks_verify_before_merging_main_then_selects_local_fast_lane(self):
        text = skill("ship")
        self.assertLess(text.index("verify-state.py check"), text.index(INTEGRATION_COMMAND))
        self.assertLess(text.index(INTEGRATION_COMMAND), text.index("pnpm ship:check"))
        self.assertIn("checkout's own `package.json`", text)
        self.assertIn("--add-tests", text)
        self.assertIn("--reason", text)
        self.assertNotIn("--force-with-lease", text)
        self.assertNotIn("git rebase", text)
        self.assertIn("gh pr merge <PR> --squash --match-head-commit <SHA>", text)

    def test_a_branch_adopts_its_own_squash_merged_prs_before_merging_main(self):
        # PR #1422: 82 conflicted paths, 80 of them the branch's squash-merged #1397
        # replayed against its own later edits.
        adopt = "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py adopt-squash"
        for name in ("ship", "merge"):
            text = skill(name)
            self.assertIn(adopt, commands(text), name)
            self.assertLess(text.index("git fetch origin main"), text.index(adopt), name)
            self.assertLess(text.index(adopt), text.index(INTEGRATION_COMMAND), name)
        self.assertIn("run `adopt-squash` and the merge command", skill("ship"),
                      "the re-merge when main moved adopts too")

    def test_the_check_after_the_merge_reuses_unchanged_passes_and_is_never_skipped(self):
        step = skill("ship").split("**Local validation.**", 1)[1].split("Before every push", 1)[0]
        self.assertIn("byte-identical", step)
        self.assertIn("Never skip it", step)
        self.assertIn("never replace it with commands picked by hand", step)

    def test_deploy_reads_exact_sha_health_before_join_or_tag(self):
        text = skill("deploy")
        self.assertIn("repos/:owner/:repo/commits/<SHA>/statuses", text)
        self.assertIn("endoxia/main-health", text)
        self.assertIn("failure", text)
        self.assertIn("pending", text)
        self.assertLess(text.index("endoxia/main-health"), text.index("**A run that already builds it.**"))

    def test_verify_uses_fast_check_and_has_no_rearming_on_main_merge(self):
        text = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn("pnpm ship:check", text)
        self.assertIn("before merging main", text)
        self.assertIn("generic.md", text)
        refactor = read(os.path.join(ROOT, "skills/refactor/phases/05-verify.md"))
        self.assertIn("skills/feature/phases/04-verify.md", refactor)

    def test_briefs_run_the_touched_test_files_not_the_suite(self):
        # Owner, 2026-10-21: keep checks small. Agents run the test files they touched,
        # `ship:check` selects the rest, and a mutation proof runs one mutant against
        # the one test file that must turn red. A repository-root `vitest run <file>`
        # matched no test in Endoxia and exited 0.
        # The one exemption, and why: generic.md is the merge gate of a project that has
        # no selecting check (no `ship:check`). Nothing there selects what a change
        # reaches, so the gate runs the whole suite; it says so itself, and it is not
        # the build loop.
        exempt = {os.path.join("skills", "ship", "generic.md"): "the merge gate of a project with no selecting check"}
        for relative, reason in exempt.items():
            self.assertIn(reason, " ".join(read(os.path.join(ROOT, relative)).split()), relative)
        for folder in ("skills", "agents"):
            for directory, _, files in os.walk(os.path.join(ROOT, folder)):
                for name in files:
                    path = os.path.join(directory, name)
                    if os.path.relpath(path, ROOT) in exempt:
                        continue
                    self.assertEqual(unreasoned_suite_runs(read(path)), [], path)
        for relative in ("skills/feature/SKILL.md", "skills/feature/phases/02-build.md",
                         "skills/refactor/SKILL.md", "skills/tdd-methodology/SKILL.md"):
            prose = " ".join(read(os.path.join(ROOT, relative)).split()).lower()
            self.assertIn("one mutant at a time, against the one test file that must turn red", prose, relative)
        for name in ("component", "service", "hook", "route", "schema"):
            self.assertIn("per-file test command", read(os.path.join(ROOT, "agents", f"{name}-builder.md")), name)

    def test_the_suite_contract_reads_every_bare_spelling(self):
        # Review finding (Codex, 2026-10-22): the contract matched only some spellings, so
        # `pnpm typecheck` or `pnpm -r test` in a brief stayed green. Mutation: the old
        # pattern (`pnpm test` and `turbo run test` only) misses these.
        for line in ("Run the whole suite: `pnpm test`.", "Then `pnpm typecheck`.", "Run `pnpm -r test`.",
                     "Run `turbo run test`.", "Run `turbo run typecheck`.", "`pnpm run typecheck && pnpm lint`",
                     "pnpm --recursive test", "Run `npm test`, then `yarn typecheck`."):
            self.assertNotEqual(unreasoned_suite_runs(f"Intro.\n\n{line}\n"), [], line)
        for line in ("Run `pnpm --filter @endoxia/core test <files>`.", "`pnpm test src/a.test.ts`",
                     "allowed-tools: Bash(pnpm test*)", "pnpm ship:check"):
            self.assertEqual(unreasoned_suite_runs(f"Intro.\n\n{line}\n"), [], line)

    def test_a_suite_run_is_one_that_names_no_test_file(self):
        # Review round 2 (Codex, 2026-10-22): `pnpm --filter <pkg> test` with no file is a
        # whole package suite, and `npm test -- <file>` is per-file. A run is suite-wide
        # unless it names a test file or path. Mutation: the spelling pattern of round 1
        # accepts the first group and refuses the second.
        for line in ("Run `pnpm --filter @endoxia/core test` after every edit.",
                     "Run pnpm --filter @endoxia/core test after every edit.",
                     "Then `pnpm -F web test --run`.", "Run `npm run test -- --coverage`.",
                     "`turbo run test --filter=@endoxia/web`", "Run `pnpm --filter @endoxia/web typecheck`."):
            self.assertNotEqual(unreasoned_suite_runs(f"Intro.\n\n{line}\n"), [], line)
        for line in ("Run `npm test -- src/foo.test.ts`.", "`pnpm --filter @endoxia/<pkg> test <files>`",
                     "Run `yarn test packages/core/src/a.test.ts && pnpm lint`.",
                     "`pnpm --filter @endoxia/core test src/a.test.ts src/b.test.ts`",
                     "Run `npm test -- <test-file>`.", "Endoxia: `pnpm --filter <pkg> test <file>…`). Then"):
            self.assertEqual(unreasoned_suite_runs(f"Intro.\n\n{line}\n"), [], line)

    def test_only_an_explicit_stated_reason_in_the_same_sentence_exempts(self):
        # Review round 2 (Codex, 2026-10-22): any paragraph with the words "stated reason"
        # exempted its commands, a negation included. Mutation: the paragraph exemption
        # accepts every line of the first group.
        for text in ("No stated reason is needed. Run pnpm test after every implementation edit.",
                     "Run `pnpm test`; no stated reason needed.",
                     "Run `pnpm test` without a stated reason.",
                     "A suite needs a stated reason. Run `pnpm test` after every edit.",
                     "Needs a stated reason.\n\nRun `pnpm test`.",
                     "Run `pnpm typecheck`, which does not need a stated reason."):
            self.assertNotEqual(unreasoned_suite_runs(text), [], text)
        for text in ("`pnpm typecheck` needs a stated reason (a changed tsconfig).",
                     "A whole-suite run (`pnpm -r test`)\nneeds a stated reason.",
                     "Run `turbo run test` only with a stated reason: a changed test config."):
            self.assertEqual(unreasoned_suite_runs(text), [], text)

    def test_only_a_test_file_selects_never_an_option_value(self):
        # Review round 3 (Codex, 2026-10-22): an option's path-like value counted as a
        # selected file. A selection is a test file (`*.test.*`, `*.spec.*`, a
        # `__tests__/` path) or the `<file>`/`<files>` placeholder. Mutation: reading
        # any path-like word as a file accepts the first group.
        for line in ("Run `pnpm test -- --coverage.reportsDirectory coverage/`.",
                     "Run `pnpm test --reporter ./reporters/dot.js`.", "Run `pnpm test src/utils/`.",
                     "Run `pnpm test src/a.ts`.", "Run `npm test -- --config vitest.config.ts`."):
            self.assertNotEqual(unreasoned_suite_runs(f"Intro.\n\n{line}\n"), [], line)
        for line in ("Run `pnpm test src/a.spec.tsx`.", "Run `pnpm test src/__tests__/a.ts`.",
                     "Run `pnpm test --reporter dot src/a.test.ts`.", "Run `pnpm test <files>`."):
            self.assertEqual(unreasoned_suite_runs(f"Intro.\n\n{line}\n"), [], line)

    def test_any_negation_in_the_sentence_voids_the_exemption(self):
        # Review round 3 (Codex, 2026-10-22): a negation more than three words before
        # "stated reason" still exempted. Mutation: a bounded look-back accepts these.
        for text in ("Run `pnpm test`: no validation command needs a stated reason.",
                     "Never skip `pnpm typecheck`; it needs a stated reason to skip.",
                     "Run `pnpm test`, which nobody thinks needs a stated reason.",
                     "Run `pnpm test`; it isn't something that needs a stated reason."):
            self.assertNotEqual(unreasoned_suite_runs(text), [], text)
        self.assertEqual(unreasoned_suite_runs("`pnpm test` needs a stated reason (a changed config)."), [])

    def test_agents_can_bring_up_their_own_stack(self):
        # Owner, 2026-10-20: an agent working a goal brings up its stack itself
        # instead of waiting for a person to type /spinup.
        text = read(os.path.join(ROOT, "skills/spinup/SKILL.md"))
        self.assertNotIn("disable-model-invocation", text.split("\n---", 1)[0])
        verify = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn("/spinup", verify)
        self.assertNotIn("worktree-stack.sh down", verify)

    def test_codex_review_runs_at_extra_high_reasoning_effort(self):
        # Owner, 2026-10-20: the review is what VERIFY leans on, so the Codex
        # reviewer runs at xhigh; T3's default is low.
        verify = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn('"options": [{ "id": "reasoningEffort", "value": "xhigh" }]', verify)

    def test_review_reports_its_coverage_and_the_parent_checks_it(self):
        brief = read(os.path.join(ROOT, "agents/correctness-reviewer.md"))
        self.assertIn("Applicable rules:", brief)
        self.assertIn("Behaviour traced:", brief)
        self.assertIn("Not read, or only skimmed:", brief)
        verify = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn("Coverage", verify)

    def test_the_verdict_is_bound_to_the_reviewed_contents(self):
        verify = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        self.assertIn("scripts/verify-state.py record", verify)
        for name in ("ship", "quickship", "merge"):
            self.assertIn("scripts/verify-state.py check", skill(name), name)
            # What is committed must be what was reviewed, not only what is on disk.
            self.assertIn("verify-state.py check --commit", skill(name), name)
            # Merging main in is accepted only as git's clean merge, never assumed.
            self.assertIn("verify-state.py integrate", skill(name), name)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts/verify-state.py")))

    def test_verify_launches_only_agents_that_exist(self):
        text = read(os.path.join(ROOT, "skills/feature/phases/04-verify.md"))
        for agent in ("correctness-reviewer", "runtime-verifier"):
            self.assertIn(agent, text)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "agents", agent + ".md")), agent)
        retired = ("code-quality-reviewer", "jit-e2e-author", "rehydration-checker",
                   "temporal-checker", "dead-code-finder")
        for directory, _, files in os.walk(os.path.join(ROOT, "skills")):
            for name in files:
                body = read(os.path.join(directory, name))
                for agent in retired:
                    self.assertNotIn(f"`{agent}`", body, os.path.join(directory, name))
        for agent in retired:
            self.assertFalse(os.path.exists(os.path.join(ROOT, "agents", agent + ".md")), agent)


class MergePreparationContractTest(unittest.TestCase):
    def test_existing_pr_preparation_stays_under_merge_authorization(self):
        from test_release_approval_gate import bash_decision, skill_decision
        # A typed merge authorizes its own merge command, not a nested ship.
        transcript = Transcript().typed("merge")
        self.assertIsNotNone(skill_decision(transcript, "ship"))
        self.assertIsNone(bash_decision(
            transcript, "gh pr merge 42 --squash --match-head-commit abc"))
        text = skill("merge")
        self.assertNotRegex(text, r"(?:run|invoke) /ship\b")
        self.assertNotIn("gh pr create", text)
        self.assertIn(INTEGRATION_COMMAND, text)
        self.assertIn("git push origin <BRANCH>", text)
        self.assertIn("headRefOid", text)
        self.assertIn("skills/ship/generic.md", text)
        self.assertLess(text.index("verify-state.py check"),
                        text.index(INTEGRATION_COMMAND))
        self.assertLess(text.index("git push origin <BRANCH>"),
                        text.index("**Wait for GitHub"))


class ConventionalIntegrationTest(unittest.TestCase):
    """Exercise published commands through Git's real commit-msg hook boundary."""

    def repository(self, path):
        # No network, user hooks, signing agent or inherited worktree Git vars.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})

        def git(*args):
            # Same per-invocation budget as the release hook (hooks/hooks.json).
            return subprocess.run(["git", "-C", str(path), *args], env=env,
                                  text=True, capture_output=True, timeout=30)

        def succeed(*args):
            result = git(*args)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result

        succeed("init", "--initial-branch=main")
        succeed("config", "user.name", "Integration fixture")
        succeed("config", "user.email", "fixture@example.invalid")
        hook = path / ".git" / "hooks" / "commit-msg"
        hook.write_text(r"""#!/bin/sh
message=$(head -n 1 "$1")
printf '%s\n' "$message" >> .git/commit-hook-subjects
if ! printf '%s\n' "$message" | grep -Eq '^(feat|fix|refactor|chore|docs|test|ci|style|perf|build|revert)(\(.+\))?: .+'; then
  echo 'strict conventional commit hook refused the subject' >&2
  exit 1
fi
""")
        hook.chmod(0o755)
        (path / "base.txt").write_text("base\n")
        succeed("add", "base.txt")
        succeed("commit", "-m", "chore: create fixture")
        succeed("checkout", "-b", "feature")
        (path / "feature.txt").write_text("feature\n")
        succeed("add", "feature.txt")
        succeed("commit", "-m", "feat: add feature")
        succeed("checkout", "main")
        (path / "main.txt").write_text("main advanced\n")
        succeed("add", "main.txt")
        succeed("commit", "-m", "feat: advance main")
        succeed("update-ref", "refs/remotes/origin/main", "HEAD")
        succeed("checkout", "feature")
        return git

    def test_default_merge_subject_is_rejected_by_a_strict_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            git = self.repository(path)
            result = git("merge", "--no-edit", "origin/main")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("strict conventional commit hook refused", result.stderr)
            self.assertIn("Merge remote-tracking branch", (path / ".git" / "commit-hook-subjects").read_text())

    def test_published_integration_commands_merge_with_hooks_enabled(self):
        for relative in ("skills/ship/SKILL.md", "skills/ship/generic.md",
                         "skills/merge/SKILL.md", "skills/quickship/SKILL.md"):
            text = read(os.path.join(ROOT, relative))
            integrations = [command for command in commands(text)
                            if command.startswith("git merge ") and "origin/main" in command]
            self.assertTrue(integrations, relative)
            for command in integrations:
                with self.subTest(skill=relative, command=command), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)
                    git = self.repository(path)
                    result = git(*shlex.split(command)[1:])
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    subject = git("log", "-1", "--format=%s").stdout.strip()
                    self.assertEqual(subject, "chore: integrate origin/main before shipping")
                    self.assertEqual(git("rev-list", "--parents", "-n", "1", "HEAD").stdout.count(" "), 2)
                    self.assertEqual((path / ".git" / "commit-hook-subjects").read_text().splitlines()[-1], subject)
                    self.assertEqual(git("merge-base", "--is-ancestor", "origin/main", "HEAD").returncode, 0)
                    self.assertEqual(git("status", "--porcelain").stdout, "")
