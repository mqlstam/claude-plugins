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

## Context (pre-computed)

- Branch: !`git branch --show-current`

## Setup

1. If branch starts with `worktree-`, use it as-is. Otherwise, create a feature branch: `git checkout -b feat/$ARGUMENTS`
2. Ask the user which layers are in scope:
   - [ ] Schema (database changes)
   - [ ] Services (backend logic)
   - [ ] Routes (API endpoints)
   - [ ] Hooks (frontend queries)
   - [ ] Components (UI)
3. Create tasks for tracking — one per phase, only for in-scope work.
4. **Fetch library docs** before any building:
   a. Check if the project CLAUDE.md has a `Library Docs` table (with `llms.txt` URLs or MCP server names). If it does, use it as the source of truth for docs URLs.
   b. If no docs table exists, scan `package.json` for major dependencies and look up their `llms.txt` at `https://<library-domain>/llms.txt`.
   c. Fetch the `llms.txt` index for each library relevant to the in-scope layers (use MCP servers when available — they take priority over fetching).
   d. Deduplicate — fetch each library once. Skip libraries already fetched in this conversation.

## Phases

| # | Phase | What happens | Details |
|---|-------|-------------|---------|
| 1 | SPEC | Define types, API contract, task cards | Read [phases/01-spec.md](phases/01-spec.md) |
| 2 | BUILD | Parallel TDD builders per layer | Read [phases/02-build.md](phases/02-build.md) |
| 3 | WIRE | Connect layers, add routing | Read [phases/03-wire.md](phases/03-wire.md) |
| 4 | REVIEW | Subagent code review + fixes | Read [phases/04-review.md](phases/04-review.md) |
| 5 | INTEGRATION | Final validation, all tests, browser check | Read [phases/05-integration.md](phases/05-integration.md) |

When entering a phase, read its detail file for full instructions.

## Rules

1. **Never skip phases** — each must complete before the next
2. **Parallel where possible** — launch multiple builders in a single message
3. **Use agents** — delegate to specialized subagent types
4. **Track progress** — update tasks on phase entry and completion
5. **No backward compatibility** — delete old code completely. No re-exports, no `_deprecated` prefixes, no `// removed` comments, no shims. If something is replaced, the old version is gone
