---
name: refactor
description: >-
  Start a refactoring workflow. Default is a single-agent build: the migration, with a
  characterization test only where no existing test and no type holds the behaviour
  being moved, then VERIFY. Pass --layers to
  fan out per-layer test writers and module migrators. Use when the user wants to
  refactor, restructure, extract, or reorganize existing code.
argument-hint: <target> [--layers]
---

# Refactor: $ARGUMENTS

## Setup

**Workspace:** Stay on the current branch — never `git checkout`, `git switch`, `git checkout -b`, `git stash`, or create branches. The user picks the workspace at session start; the skill must respect that choice.

1. Create tasks for tracking — one per phase.
2. **Read the real docs** for the libraries the refactor target uses:
   a. Scan imports in the target files to determine which libraries are involved, and
      find the version this project runs of each (lockfile, Dockerfile pins).
   b. Read the documentation for THAT version: the guides that explain the concept, show
      worked examples and set out the options, not only type definitions. Look first for
      docs shipped inside the installed package; otherwise the docs site, after checking
      which version it documents (a newer major means finding our version's path, or the
      repository's docs at our tag).
   c. Read pages as raw text (the `.md` twin, the source on GitHub,
      `curl -H 'Accept: text/markdown'`); WebFetch returns a summary.
   d. If the project has its own docs rule (e.g. `.claude/rules/library-docs.md`), follow it.
   e. **This is critical for refactors**: the existing code may use a pattern an older
      version needed, so confirm the replacement against the docs of the version we run.
      Write in the plan, per library, the version and the guides you read.

## Build shape — single agent by default

**Default: ONE agent (you) performs the migration**, module by module, and writes a
characterization test only where "The bar, and which tests" below asks for one. Not a per-layer test-writer fan-out, and not a per-module migrator fan-out.

Measured position, not preference: a controlled study plus this user's own session data
(**8-10× the token volume, 46 sub-agents in a session against 1**) found no quality gain
from rigid red-green sequencing or per-layer builder agents. What earns its cost is a
run on the real surface, and VERIFY.

The ANALYZE fan-out is different and stays: `dependency-mapper`, `caller-finder`,
`test-scanner` and `impact-assessor` are **read-only searches over disjoint questions**,
which is the case where parallel sub-agents genuinely pay. Fanning out to FIND is
cheap; fanning out to WRITE is what costs.

### `--layers` — opt in for genuinely independent modules

Pass `--layers` when the migration touches modules that do not share state, types or
files and can be migrated without reading each other's output. Then fan out
`refactor-test-writer` per layer and `module-migrator` per module, in a single message,
and reconcile. If you end up merging their assumptions by hand, they were not
independent — finish it yourself and say so.

## The bar, and which tests

**The bar in your brief is the whole bar.** Add no test, check, pin, gate, proof or
review round above it. With no bar stated: the change is no worse than its base and
its own tests pass. Hold or block only for a failing run at this head or a fault a
user would hit.

**A test is optional.** Write one only when the brief asks for it or the fault has an
obvious, cheap, local test target, and then by the keep rule: a test stays only if it
catches a failure that (1) can really happen, (2) hurts when it does, and (3) is not
already caught elsewhere or cheaper elsewhere: another test, the model e2e runs,
staging's roll and probe, the gate before production. Everything else is deleted, not
moved or quarantined. Name the failure a test catches in its title. No new test is
better than a bad test.

**The default proof of a change is a run on the real surface**: the worktree's stack,
the route, the command. File mtimes, self-reports, cached output and "it compiles"
are not proof. A test you find that still passes when the behaviour it checks is
broken is fixed or deleted.

For a refactor the faults are a caller that now sees other behaviour, a persisted
format that moved, a path that no longer runs. ANALYZE's `test-scanner` says which an
existing test already holds, and the type system holds the signatures. A
characterization test is written only where neither holds the behaviour being moved:
green on the old structure, unchanged after. A test of the old internals goes with them.

## Parallelism overview

```
Phase 1: ANALYZE ──┬── dependency-mapper ──┐
                   ├── caller-finder ──────┼── merge → plan     (fan out to FIND: cheap)
                   ├── test-scanner ───────┤
                   └── impact-assessor ────┘

Phase 2: TEST  ─── single agent; only the gaps (--layers to fan out)
Phase 3: REFACTOR ─ sequential (dependencies)
Phase 4: MIGRATE ── single agent (--layers to fan out)

Phase 5: VERIFY ─── the feature workflow's VERIFY, told what the refactor replaces
```

## Phases

| # | Phase    | Parallel?                    | Details                                       |
|---|----------|------------------------------|-----------------------------------------------|
| 1 | ANALYZE  | Always (4 read-only agents)  | [phases/01-analyze.md](phases/01-analyze.md)  |
| 2 | TEST     | Only with `--layers`; may be empty | [phases/02-test.md](phases/02-test.md)        |
| 3 | REFACTOR | Never                        | [phases/03-refactor.md](phases/03-refactor.md)|
| 4 | MIGRATE  | Only with `--layers`         | [phases/04-migrate.md](phases/04-migrate.md)  |
| 5 | VERIFY   | Review + runtime check       | [phases/05-verify.md](phases/05-verify.md)    |

When entering a phase, read its detail file.

## Rules

1. **Single-agent build by default**; `--layers` only for genuinely independent modules
2. **Fan out to FIND, not to WRITE** — ANALYZE is parallel because its questions are disjoint and read-only
3. **Track progress** — update tasks on phase entry and completion
4. **No backward compatibility** — delete old code, old exports, old types. No
   re-exports, no `_deprecated` prefixes, no `// removed` comments, no shims. Update
   ALL callers in the MIGRATE phase instead
5. **Never `sleep`-poll a long command** — run it with `run_in_background: true`;
   its completion wakes you, so carry the workflow on from it in that turn. Never end the turn waiting on something with no local task (a GitHub run, a
   remote job): wrap that wait in a local background command that exits on its terminal
   state (the plugin context's "A background step is yours" rule)
