# The head's one proof

A PR head reaches main through **one pre-queue proof** and, where main requires the
merge queue, **the queue's proof of the merge result**. Nothing else re-proves it.

Measured on 2026-11-04 (13 landings): a head was proved by VERIFY's `ship:check`, by
`ship:check` again after each re-integration of main (most of them after a conflict
in an index file only), by a CI PR proof on the same head, and by the queue. The
queue caught both Linux-only defects of that night; the local re-runs after
re-integration caught neither, and each paid the floor again.

## 1. Exactly one proof per head

The proof is the project's full change check of the head: `pnpm ship:check` when the
checkout declares it, otherwise `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`.
**Where it runs** is the project's call: read its verify-phase rule
(`.claude/rules/verify-phase.md`, section "The head's one proof") when it has one.
It names the default runner (a Linux worker, or this checkout) and, when the project
has a CI proof of a PR head, when that runs **instead**. Without such a rule, run it
in this checkout.

**Never both on one head.** A head proved by `ship:check` is not also sent to the CI
PR proof, and a head the CI PR proof proved does not run `ship:check` again. The PR
body's validation evidence names which proof ran, on which commit, and its result
(exit `0`, or the run's URL).

## 2. When it runs

- In VERIFY, beside the reviewers, on the tree under review (VERIFY step 2c).
- Again only when the author changes the reviewed contents: a fix (VERIFY step 4).
  That is a new head of author work, and it gets its one proof.

## 3. Integrating main

Read `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py mode` once for the landing.

- **Through the merge queue** (`0` `merge_queue=required`): an integration of main
  runs **no new full proof** when it is
  - a clean merge (`verify-state.py integrate` exits `0`), or
  - conflict-only: its only change beyond git's merge is a resolution that keeps both
    sides (an index line, a generated file, adjacent edits). It still gets the
    targeted correctness review `integrate` asks for when the resolution changes
    behaviour.

  The queue's required check builds the PR on current main and proves that merge
  result before main moves, so a full proof of the integration would prove nothing
  the queue does not. A resolution that writes new code to make the two sides work
  together is author work: treat it as a fix (section 2).
- **Directly** (`1` `merge_queue=none`, or a direct push to main): nothing else
  proves the merge result, so the merged tree is validated once after the
  integration, as the skill says. `ship:check` reuses every pass whose input
  contents are byte-identical, so it re-runs only what the merge reaches.
- `2`: GitHub could not be read. Report it and STOP; never guess.

## 4. What stays

VERIFY's independent review and runtime verification, `verify-state.py`'s binding of
the verdict to the exact contents, and the merge queue's required proof. This file
removes only the repeated full proof of the same work.
