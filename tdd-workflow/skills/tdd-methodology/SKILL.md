# Vertical Slice TDD

A methodology for building complete features from database to UI, held by tests.

## Core principle

Every feature is a **vertical slice** through all layers:

```
Component -> Hook -> Route -> Service -> Schema
```

Each layer's behaviour is asserted by a test. But the layers are a map of the
SURFACE, not a schedule — they do not tell you how many agents to use or in what
order to write things.

## What was measured, and what changed

A controlled study, plus this user's own session data, found **no quality gain** from:

- **rigid red-green ceremony per layer** — writing a failing test, watching it fail,
  then the minimum code, per layer, as a required sequence;
- **per-layer builder sub-agents** — the fan-out cost **8-10× the token volume** and
  **46 sub-agents in a session against 1**, to produce handoffs that then had to be
  reconciled.

Three things DID earn their cost, and they are the methodology now:

1. **Tests as the contract.** The test asserts the slice's acceptance criterion. If
   you cannot say what it would catch, it is not a test.
2. **Mutation proof.** Revert the line, watch the named test go red, restore, watch it
   go green. A test that passes with and without the change is decoration.
3. **VERIFY.** The parallel, scope-gated specialist fan-out over the finished diff.

So: **write the test and the implementation together**, at the granularity the
behaviour actually has. Fan out (`--layers`) only when the slice spans surfaces that
genuinely do not share state, types or files.

The one place fan-out is still the default is **reading**: refactor's ANALYZE phase
runs four read-only agents over four disjoint questions. Fanning out to FIND is cheap.
Fanning out to WRITE is what costs.

## Mutation proof — the ceremony worth keeping

```bash
cp path/to/file.ts /tmp/file.ts.bak     # snapshot FIRST
# revert the line that implements the behaviour
pnpm exec vitest run path/to/file.test.ts   # MUST go red, naming the behaviour
cp /tmp/file.ts.bak path/to/file.ts     # restore
pnpm exec vitest run path/to/file.test.ts   # green again
```

**Never restore with `git checkout -- <file>`.** It reverts the file to HEAD, which
destroys every uncommitted change in it — including work that had nothing to do with
the mutation.

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
| `/feature <name> [--layers]`| New feature; single-agent build by default                     |
| `/refactor <name> [--layers]`| Refactor; ANALYZE fans out, the build does not                |
| `/ship`                     | Gate (reusing what VERIFY proved), commit, push, PR, merge     |
| `/quickship`                | Same gates, straight to main                                   |
| `/merge`                    | Squash-merge an existing PR                                    |
| `/deploy`                   | Release main via a `deploy-*` tag — the only thing that deploys (Endoxia: to STAGING; production is `/promote`) |
| `/spinup` / `/teardown`     | This worktree's private runnable stack                         |
| `/validate`                 | Check slice completeness                                       |

## Gate economics (why /ship is fast after a green VERIFY)

The gate runs **once per content**. VERIFY's `invariant-runner` records each command it
ran green against a key of `{commit SHA, tree hash}` — the tree hash covering
uncommitted and untracked files, because at gate time nothing is committed yet. `/ship`
skips exactly those commands, out loud. `.husky/pre-push` skips its sections only when
`/ship` PROMOTED that record onto the pushed commit, which re-verifies the tree hash —
so a hand push, an amend, or any edit after the gate gets the full hook.

One heavy gate runs at a time across all worktrees (`scripts/gate-lock.mjs`): two at
once is slower than queueing them and produces verdicts that are about the load, not
the code.

And never `sleep`-poll a long command. Run it with `run_in_background: true` and wait
for the completion notification — polling was **46% of measured tool wall-clock**.
