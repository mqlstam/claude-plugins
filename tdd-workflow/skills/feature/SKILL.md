---
name: feature
description: >-
  Start a TDD feature workflow with parallel builders across schema, service,
  route, hook, and component layers. Use when the user wants to build a new
  feature, add functionality, or implement a vertical slice.
argument-hint: <feature-name>
---

# Feature: $ARGUMENTS

TDD feature workflow with parallel builders. Each phase must complete before the next.

## Setup

**Workspace:** Stay on the current branch — never `git checkout`, `git switch`, `git checkout -b`, `git stash`, or create branches. The user picks the workspace (worktree, feature branch, or main) at session start; the skill must respect that choice. If the user wants isolation, they spawn with `--worktree` or pass `isolation: "worktree"` on parallel sub-agent invocations.

1. Determine which layers are in scope by analyzing the feature requirements (schema, services, routes, hooks, components). Proceed with all relevant layers — do not ask for confirmation.
2. Create tasks for tracking — one per phase, only for in-scope work.
3. **Verify library patterns** before any building:
   a. Check the project CLAUDE.md for a `Library Docs` table with lookup methods per library.
   b. If no table exists, search the web for each library's `llms.txt` (e.g., `https://<library-domain>/llms.txt`).
   c. For each library relevant to the in-scope layers, use the specified method to search for the specific pattern/concept you're about to implement.
   d. This applies equally to **modifying existing code** — if the existing pattern might be outdated, verify it against current docs before extending it.

## Phases

| # | Phase | What happens | Details |
|---|-------|-------------|---------|
| 1 | SPEC | Define types, API contract, task cards | Read [phases/01-spec.md](phases/01-spec.md) |
| 2 | BUILD | Parallel TDD builders per layer | Read [phases/02-build.md](phases/02-build.md) |
| 3 | WIRE | Connect layers, add routing | Read [phases/03-wire.md](phases/03-wire.md) |
| 4 | VERIFY | Parallel fan-out: 5 specialists check the diff | Read [phases/04-verify.md](phases/04-verify.md) |

When entering a phase, read its detail file for full instructions.

## Rules

1. **Never skip phases** — each must complete before the next
2. **Parallel where possible** — launch multiple builders in a single message
3. **Use agents** — delegate to specialized subagent types
4. **Track progress** — update tasks on phase entry and completion
5. **No backward compatibility** — delete old code completely. No re-exports, no `_deprecated` prefixes, no `// removed` comments, no shims. If something is replaced, the old version is gone
