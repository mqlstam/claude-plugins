# Vertical Slice Method

A methodology for building complete features from database to UI.

## The bar and the keep rule

**The bar in your brief is the whole bar.** Add no test, check, pin, gate, proof or
review round above it. With no bar stated: the change is no worse than its base and
its own tests pass. Hold or block only for a failing run at this head or a fault a
user would hit.

A test stays only if it catches a failure that (1) can really happen, (2) hurts when
it does, and (3) is not already caught elsewhere or cheaper elsewhere: another test,
the model e2e runs, staging's roll and probe, the gate before production. Everything
else is deleted, not moved or quarantined.

A test is optional, and no new test is better than a bad test. Nothing here asks for
a test per file, per layer or per acceptance criterion, and nothing counts tests.
Deleting a test that fails the keep rule is correct work.

## Core principle

Every feature is a **vertical slice** through all layers:

```
Component -> Hook -> Route -> Service -> Schema
```

The layers are a map of the SURFACE, not a schedule — they do not tell you how many
agents to use, in what order to write things, or where a test belongs.

## What was measured, and what changed

A controlled study, plus this user's own session data, found **no quality gain** from:

- **rigid red-green ceremony per layer** — writing a failing test, watching it fail,
  then the minimum code, per layer, as a required sequence;
- **per-layer builder sub-agents** — the fan-out cost **8-10× the token volume** and
  **46 sub-agents in a session against 1**, to produce handoffs that then had to be
  reconciled.

Two things DID earn their cost, and they are the methodology now:

1. **A run on the real surface.** The default proof of a change is the worktree's
   stack, the route or the command doing the thing; file mtimes, self-reports, cached
   output and "it compiles" are not proof. A test is written only when the brief asks
   or the fault has an obvious, cheap, local target, and then by the keep rule.
2. **VERIFY.** An independent correctness review of the finished diff and, when the
   change is observable, a check that uses it on a real stack, both beside the
   local checks.

So: **build at the granularity the behaviour actually has** and run it. Fan out (`--layers`) only when the slice spans surfaces that
genuinely do not share state, types or files.

The one place fan-out is still the default is **reading**: refactor's ANALYZE phase
runs four read-only agents over four disjoint questions. Fanning out to FIND is cheap.
Fanning out to WRITE is what costs.

## Running tests: the files you touched, not the suite

- **While building, run the test files you touched and the direct tests of the
  modules you changed**, with the project's per-file test command. The project's own
  rule names it (Endoxia: `.claude/rules/running-tests.md`, which runs
  `pnpm --filter <pkg> test <file>…`). Check that the output names your file: in a
  workspace, `vitest run <file>` from the repository root can match no test and
  still exit 0, a run that proves nothing.
- **The project's pre-queue check selects the rest** at VERIFY (`pnpm precheck` or
  `ship:check`, `skills/ship/pre-queue-check.md`: importers, snapshot owners, a
  package suite when a test config or shared helper changed).
  Running that selection by hand first does the gate's work twice.
- **A whole package or repository suite needs a stated reason** (a test config, a
  setup file or a shared testing helper changed; a failure the gate reported is being
  reproduced), and runs one at a time.

Several sessions building at once share one machine: whole suites each started by an
agent took it to a load of 37-83 with no idle CPU (Endoxia,
2026-10-21). The owner's decision is to keep the work small, not to add limits.

## Layer responsibilities

### Schema (database)
- Schema definitions and migrations; expand-only where the deploy migrates before it rolls
- Always `tenantId` for multi-tenancy; always timestamps

### Service (business logic)
- Pure business logic; input → output; always filters by `tenantId`

### Route (API)
- Handlers, Zod validation at the trust boundary, auth, calls the service layer

### Hook (data fetching)
- TanStack Query hooks, query keys, optimistic updates, error handling

### Component (UI)
- React components, loading/error states, design-system primitives
- Every `use*()` above every branch and early return

## Escapes: what a pre-merge gate cannot catch

**~80% of the fixes that landed after a merge were invisible to any static pre-merge
gate.** They were runtime, data-shaped, or only visible against a real stack. The
conclusion is NOT "add a 67th static check" — it is that the runtime lane (a real
stack, a real request, the logs and the database) is where the remaining defects live.
Reach for it before reaching for another grep-shaped gate.

## Available skills

| Skill                       | Purpose                                                        |
| --------------------------- | -------------------------------------------------------------- |
| `/feature <name> [--layers]`| New feature; single-agent build, proved on the real surface    |
| `/refactor <name> [--layers]`| Refactor; ANALYZE fans out, the build does not                |
| `/ship`                     | Land the PR the owner reviewed: that exact head, then its staging deploy (VERIFY opens the PR with its evidence) |
| `/quickship`                | Same local checks, push directly to main (through a PR and the merge queue where main requires one) |
| `/merge`                    | Squash-merge an existing PR (directly, or through the merge queue) |
| `/deploy`                   | Release main via a `deploy-*` tag — the only thing that deploys (Endoxia: to STAGING; production is `/promote`) |
| `/spinup` / `/teardown`     | This worktree's private runnable stack                         |
| `/validate`                 | Check the slice: its real failures caught, its layers connected |

## Local checks and release gates

Read the checkout's own `package.json`. VERIFY runs its pre-queue check beside
the review (`pnpm precheck` when declared, else `ship:check`, else the generic
checks: `skills/ship/pre-queue-check.md`), and again after any fix. Nothing is
dispatched for a PR head. At READY, VERIFY opens the PR for review: it commits,
merges origin/main in, pushes and attaches the evidence; through the merge queue a
clean or conflict-only integration runs no new check, because the queue's required
check proves the merge result. /ship
then lands exactly the PR head that evidence was recorded for. Integration alone does not re-arm VERIFY.

Each worktree runs independently. Push, open the PR and squash-merge directly,
matching its exact head; where main requires GitHub's merge queue
(`scripts/merge-queue.py mode`), the same merge command enqueues that head and the
queue proves the merge result before main moves. Main's short CI checks the merged range; a red main is
reported and fixed forward through an ordinary ship. It does not block ships.
Staging refuses a known red SHA and warns on unknown health. Promote runs the
whole suite on the exact production SHA, reusing a verified green when available.

Repositories with neither keep their own lint, typecheck, formatting,
test, build and assertion commands. Merge main in rather than rewriting history.
Never sleep-poll a long command: background it and wait for its completion notice.
