# The pre-queue check

A PR head reaches main through **one local check, run before the PR is opened**, and,
where main requires the merge queue, **the queue's required check, which is the
proof**. Nothing is dispatched for a PR head, and nothing re-proves it.

## 1. Which command

Read this checkout's own `package.json` (never a remote branch's):

- it declares a `precheck` script: run `pnpm precheck`, the project's pre-queue
  check;
- otherwise it declares `ship:check`: run `pnpm ship:check`. Add `--add-tests`,
  `--add-package` or `--add-checks` only with `--reason`, for a boundary its
  selection misses; never narrow it;
- otherwise: `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md` (the repository's own
  lint, typecheck, formatting, tests, build and assertions).

Run it in this checkout. When the project has a verify-phase rule
(`.claude/rules/verify-phase.md`), it says what its check covers and what is left to
the queue; follow it. Exit `0` passes, `1` fails (it names the step), `2` refuses
(fix the named environment cause, then retry); any other exit or an interrupted run
is not a pass. A check that refuses a dirty tree runs after the commit. The PR
body's validation evidence names the command, the commit it ran on and its result.

## 2. When it runs

- In VERIFY, beside the reviewers, on the tree under review (VERIFY step 2c).
- After a fix (VERIFY step 4) it does not run again: the touched files' tests run,
  with the project's per-file test command, and the merge queue proves the head.
  Landing directly, with no queue, the merged tree is checked once (section 3).

## 3. Integrating main

Read `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py mode` once for the landing.

- **Through the merge queue** (`0` `merge_queue=required`): an integration of main
  runs **no new check** when it is
  - a clean merge (`verify-state.py integrate` exits `0`), or
  - conflict-only: its only change beyond git's merge is a resolution that keeps both
    sides (an index line, a generated file, adjacent edits). It still gets the
    targeted correctness review `integrate` asks for when the resolution changes
    behaviour.

  The queue's required check builds the PR on current main and proves that merge
  result before main moves. A red queue entry is fixed on the branch and queued
  again. A resolution that writes new code to make the two sides work together is
  author work: treat it as a fix (VERIFY step 4).
- **Directly** (`1` `merge_queue=none`, or a direct push to main): nothing else
  proves the merge result, so the merged tree is checked once after the
  integration, with the command of section 1.
- `2`: GitHub could not be read. Report it and STOP; never guess.

## 4. What stays

VERIFY's independent review and runtime verification, `verify-state.py`'s binding of
the verdict to the exact contents, and the merge queue's required check.
