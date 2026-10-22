# Phase 2: BUILD

## Goal
Implement the slice's acceptance criteria, with tests that hold them.

## Default: one agent, tests and implementation together

Work through the acceptance criteria from the slice doc. For each behaviour, write the
test and the code together, at the granularity the behaviour actually has. The test
asserts the **acceptance criterion**, not the shape of the implementation you just
wrote — if you cannot state what the test would catch, it is not a test yet.

This replaces the per-layer builder fan-out and the per-layer RED→GREEN ceremony. Both
were measured and neither paid: a controlled study plus this user's own session data
(**8-10× the token volume, 46 sub-agents in a session against 1**) found no quality
gain from rigid red-green sequencing or per-layer builders. What earns its cost is
tests-as-contract, mutation proof, and VERIFY — all kept.

Practical shape:

1. Read the slice's acceptance criteria. List them.
2. For each: write the assertion, write the code, run that test file only.
3. When they all pass, run the test files you touched and the direct tests of the
   modules you changed, nothing wider, with the project's per-file test command (its
   own rule names it; Endoxia: `pnpm --filter <pkg> test <file>…`). Check that the
   output names your files: a repository-root `vitest run <file>` can match no test
   and still exit 0. VERIFY's `ship:check` selects the rest; a whole package suite
   needs a stated reason (a test config or shared testing helper changed).
   Anything longer than ~8 minutes goes through `run_in_background: true`; wait for
   the completion notification rather than `sleep`-polling.

## `--layers` — the opt-in fan-out

Only when the slice spans surfaces that do not share state, types or files, and can be
built without reading each other's output. Launch the relevant builders IN PARALLEL, in
a single message:

| Layer     | Agent               | Gets                                           |
| --------- | ------------------- | ---------------------------------------------- |
| Schema    | `schema-builder`    | feature name, spec output, its acceptance rows |
| Service   | `service-builder`   | same                                           |
| Route     | `route-builder`     | same                                           |
| Hook      | `hook-builder`      | same                                           |
| Component | `component-builder` | same                                           |

Then reconcile their output and run the test files they touched, as in step 3. If you
find yourself merging their assumptions by hand, the layers were not independent and
the fan-out was the wrong call — say so, and finish it yourself.

## Mutation proof — before you leave this phase

One mutant at a time, against the one test file that must turn red; never a loop or
driver that runs a suite per mutant.

1. Snapshot the file: `cp <file> /tmp/<file>.bak`
2. Revert the line that implements the new behaviour
3. Re-run the covering test file alone — it MUST go red, naming the behaviour
4. Restore from the snapshot: `cp /tmp/<file>.bak <file>`
   **Never** `git checkout -- <file>` — it reverts to HEAD and destroys every
   uncommitted change in that file
5. Re-run — green

Record the mutated line and the test that caught it.

## Completion

- Every acceptance criterion has a test that asserts it
- The test files you touched pass (VERIFY's `ship:check` runs what they reach)
- Mutation proof done and recorded
- Mark build task completed; move to WIRE
