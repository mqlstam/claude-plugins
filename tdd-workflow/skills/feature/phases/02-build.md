# Phase 2: BUILD

## Goal
Implement the slice's acceptance criteria.

## Default: one agent builds the slice

Work through the acceptance criteria from the slice doc, at the granularity the
behaviour actually has, and run each on the real surface. The feature skill's "The
bar, and which tests" decides whether a test is written; none is the default.

This replaces the per-layer builder fan-out and the per-layer RED→GREEN ceremony. Both
were measured and neither paid: a controlled study plus this user's own session data
(**8-10× the token volume, 46 sub-agents in a session against 1**) found no quality
gain from rigid red-green sequencing or per-layer builders. What earns its cost is a
run on the real surface, and VERIFY.

Practical shape:

1. Read the slice's acceptance criteria. List them.
2. For each: write the code and run it on the real surface (the route, the command,
   the worktree's stack); a test only when the brief asks or the fault has an obvious,
   cheap, local target.
3. When it is built, run the test files you touched and the direct tests of the
   modules you changed, nothing wider, with the project's per-file test command (its
   own rule names it; Endoxia: `pnpm --filter <pkg> test <file>…`). Check that the
   output names your files: a repository-root `vitest run <file>` can match no test
   and still exit 0. VERIFY's pre-queue check selects the rest; a whole package suite
   needs a stated reason (a test config or shared testing helper changed).
   Anything longer than ~8 minutes goes through `run_in_background: true` rather than
   `sleep`-polling; its completion wakes you, so carry the workflow on from it in that turn.

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

## Completion

- Every acceptance criterion is met, shown by a run on the real surface
- The test files you touched pass (VERIFY's pre-queue check runs what they reach)
- Mark build task completed; move to WIRE
