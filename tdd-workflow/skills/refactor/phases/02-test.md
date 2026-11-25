# Phase 2: TEST (only the gaps)

## Goal
Before anything moves, make sure the behaviour the callers rely on is held. Most of it
already is: by an existing test (ANALYZE's `test-scanner` named them) or by the type
system. This phase writes a characterization test only for what neither holds.

## Steps
1. From the plan, list the behaviour being moved that the callers rely on.
2. For each, name what holds it: an existing test, a type, a later stage (the model e2e
   runs, staging, the gate before production). That behaviour needs nothing new.
3. For what nothing holds, ask the refactor skill's "The bar, and which tests": does
   the brief ask for a test, or is there an obvious, cheap, local target, and can the
   refactor really break it in a way that hurts? Only then write a characterization
   test: through the real code, green on the OLD structure, with the failure it
   catches in its title. Otherwise the proof is a run of the moved path on the real
   surface, before and after.
4. Run only the test files you wrote, with the project's per-file test command
   (Endoxia: `pnpm --filter <pkg> test <file>`). They pass now and must still pass
   after the refactor. The output must name them: a command that matches no test
   exits 0.

With `--layers`, launch one `refactor-test-writer` per layer that has a gap, in a single
message, each told the gap it closes. A layer without a gap gets no test writer.

## Completion
- Every behaviour being moved is held by something you can name
- New tests exist only for the gaps; a phase that wrote none is complete
- Mark test task as completed, move to REFACTOR phase
