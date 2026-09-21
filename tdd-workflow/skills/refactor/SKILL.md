---
name: refactor
description: >-
  Start a TDD refactoring workflow. Default is a single-agent build: characterization
  tests and the migration together, then mutation proof, then VERIFY. Pass --layers to
  fan out per-layer test writers and module migrators. Use when the user wants to
  refactor, restructure, extract, or reorganize existing code.
argument-hint: <target> [--layers]
---

# Refactor: $ARGUMENTS

## Setup

**Workspace:** Stay on the current branch — never `git checkout`, `git switch`, `git checkout -b`, `git stash`, or create branches. The user picks the workspace at session start; the skill must respect that choice.

1. Create tasks for tracking — one per phase.
2. **Verify library patterns** for libraries used by the refactor target:
   a. Scan imports in the target files to determine which libraries are involved.
   b. Check the project CLAUDE.md for a `Library Docs` table with lookup methods per library.
   c. If no table exists, search the web for each library's `llms.txt`.
   d. For each, look up the pattern being refactored. **This is critical for refactors**
      — the existing code may use an outdated pattern, so confirm the replacement is current.

## Build shape — single agent by default

**Default: ONE agent (you) writes the characterization tests and performs the migration**,
module by module. Not a per-layer test-writer fan-out, and not a per-module migrator fan-out.

Measured position, not preference: a controlled study plus this user's own session data
(**8-10× the token volume, 46 sub-agents in a session against 1**) found no quality gain
from rigid red-green sequencing or per-layer builder agents. What earns its cost —
tests as the contract, mutation proof, VERIFY, and the dead-code sweep — is kept in full.

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

## Mutation proof — required before VERIFY

A refactor's tests must be able to fail:

1. Snapshot the file: `cp <file> /tmp/<file>.bak`
2. Revert the line that carries the refactored behaviour
3. Re-run the covering test — it MUST go red, naming the behaviour
4. Restore from the snapshot. **Never** `git checkout -- <file>` — it reverts to HEAD
   and destroys every uncommitted change in that file
5. Re-run — green

Record the mutated line and the test that caught it.

## Parallelism overview

```
Phase 1: ANALYZE ──┬── dependency-mapper ──┐
                   ├── caller-finder ──────┼── merge → plan     (fan out to FIND: cheap)
                   ├── test-scanner ───────┤
                   └── impact-assessor ────┘

Phase 2: TEST  ─── single agent (--layers to fan out)
Phase 3: REFACTOR ─ sequential (dependencies)
Phase 4: MIGRATE ── single agent (--layers to fan out)

Phase 5: VERIFY ─── parent scopes the diff, then launches only what the diff warrants
```

## Phases

| # | Phase    | Parallel?                    | Details                                       |
|---|----------|------------------------------|-----------------------------------------------|
| 1 | ANALYZE  | Always (4 read-only agents)  | [phases/01-analyze.md](phases/01-analyze.md)  |
| 2 | TEST     | Only with `--layers`         | [phases/02-test.md](phases/02-test.md)        |
| 3 | REFACTOR | Never                        | [phases/03-refactor.md](phases/03-refactor.md)|
| 4 | MIGRATE  | Only with `--layers`         | [phases/04-migrate.md](phases/04-migrate.md)  |
| 5 | VERIFY   | Scope-gated by the parent    | [phases/05-verify.md](phases/05-verify.md)    |

When entering a phase, read its detail file.

## Rules

1. **Single-agent build by default**; `--layers` only for genuinely independent modules
2. **Fan out to FIND, not to WRITE** — ANALYZE is parallel because its questions are disjoint and read-only
3. **Mutation proof before VERIFY** — non-negotiable
4. **Track progress** — update tasks on phase entry and completion
5. **No backward compatibility** — delete old code, old exports, old types. No
   re-exports, no `_deprecated` prefixes, no `// removed` comments, no shims. Update
   ALL callers in the MIGRATE phase instead
6. **Never `sleep`-poll a long command** — run it with `run_in_background: true` and
   wait for the completion notification
