---
name: refactor
description: >-
  Start a TDD refactoring workflow with parallel analysis, test-first approach,
  and module migration. Use when the user wants to refactor, restructure,
  extract, or reorganize existing code.
argument-hint: <target>
---

# Refactor: $ARGUMENTS

TDD refactoring workflow with parallel agents. Each phase must complete before the next.

## Setup

1. Create tasks for tracking — one per phase.
2. **Verify library patterns** for libraries used by the refactor target:
   a. Scan imports in the target files to determine which libraries are involved.
   b. Check the project CLAUDE.md for a `Library Docs` table with lookup methods per library.
   c. If no table exists, search the web for each library's `llms.txt` (e.g., `https://<library-domain>/llms.txt`).
   d. For each library, use the specified method to search for the specific pattern/concept being refactored.
   e. This is critical for refactors: the existing code may use an outdated pattern. Check the docs to confirm the replacement pattern is current.

## Parallelism Overview

```
Phase 1: ANALYZE ──┬── dependency-mapper ──┐
                   ├── caller-finder ──────┼── merge → plan
                   ├── test-scanner ───────┤
                   └── impact-assessor ────┘

Phase 2: TEST ─────┬── layer-a tests ──────┐
                   ├── layer-b tests ──────┼── all RED
                   └── layer-c tests ──────┘

Phase 3: REFACTOR ─── sequential (dependencies)

Phase 4: MIGRATE ──┬── module-a migrator ──┐
                   ├── module-b migrator ──┼── merge
                   └── module-c migrator ──┘

Phase 5: REVIEW ───── slice-reviewer (subagent)

Phase 6: VERIFY ───── integration-reviewer
```

## Phases

| # | Phase | Parallel? | Details |
|---|-------|-----------|---------|
| 1 | ANALYZE | Always (4 agents) | Read [phases/01-analyze.md](phases/01-analyze.md) |
| 2 | TEST | If multi-layer | Read [phases/02-test.md](phases/02-test.md) |
| 3 | REFACTOR | Never | Read [phases/03-refactor.md](phases/03-refactor.md) |
| 4 | MIGRATE | If multi-module | Read [phases/04-migrate.md](phases/04-migrate.md) |
| 5 | REVIEW | Never | Read [phases/05-review.md](phases/05-review.md) |
| 6 | VERIFY | Never | Read [phases/06-verify.md](phases/06-verify.md) |

When entering a phase, read its detail file for full instructions.

## Rules

1. **Never skip phases** — each must complete before the next
2. **Parallel where safe** — launch independent agents in a single message
3. **Merge parallel results** — combine outputs before proceeding
4. **Track progress** — update tasks on phase entry and completion
5. **No backward compatibility** — delete old code, old exports, old types. No re-exports, no `_deprecated` prefixes, no `// removed` comments, no shims. Update ALL callers in the MIGRATE phase instead
