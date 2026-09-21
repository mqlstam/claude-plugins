---
name: feature
description: >-
  Start a TDD feature workflow. Default is a single-agent build: tests and
  implementation together against the slice's acceptance criteria, then mutation
  proof, then VERIFY. Pass --layers to fan out per-layer builders. Use when the user
  wants to build a new feature, add functionality, or implement a vertical slice.
argument-hint: <feature-name> [--layers]
---

# Feature: $ARGUMENTS

## Setup

**Workspace:** Stay on the current branch — never `git checkout`, `git switch`, `git checkout -b`, `git stash`, or create branches. The user picks the workspace (worktree, feature branch, or main) at session start; the skill must respect that choice.

1. **Read the slice doc** (`docs/slices/{NN}-{name}.md` or the equivalent) for scope
   and acceptance criteria. The acceptance criteria are what the tests assert; without
   them you are guessing at the contract.
2. Create tasks for tracking — one per phase.
3. **Verify library patterns** before any building:
   a. Check the project CLAUDE.md for a `Library Docs` table with lookup methods per library.
   b. If no table exists, search the web for each library's `llms.txt`.
   c. For each relevant library, look up the specific pattern you are about to implement.
   d. This applies equally to **modifying existing code** — if the existing pattern
      might be outdated, verify it against current docs before extending it.

## Build shape — single agent by default

**Default: ONE agent (you) writes the tests and the implementation together**, working
through the slice's acceptance criteria. Not a red-green ceremony per layer, and not a
fan-out of per-layer builders.

This is a measured position, not a preference. A controlled study plus this user's own
session data (**8-10× the token volume, 46 sub-agents in a session against 1**) found
no quality gain from rigid red-green sequencing or from per-layer builder agents. What
does earn its cost is: **tests as the contract**, **mutation proof**, and **VERIFY**.
Those are kept, in full.

So write the test and the code for a behaviour together, at whatever granularity the
behaviour actually has. Keep the test honest — it asserts the acceptance criterion,
not the implementation you just wrote.

### `--layers` — opt in when the slice genuinely spans independent surfaces

Pass `--layers` (or say so in the request) when the slice has surfaces that do not
share state, types or files and can be built without reading each other's output — for
example a DB migration plus an unrelated UI-only change. Then, and only then, fan out
the per-layer builders in a single message:

| Layer     | Agent               |
| --------- | ------------------- |
| Schema    | `schema-builder`    |
| Service   | `service-builder`   |
| Route     | `route-builder`     |
| Hook      | `hook-builder`      |
| Component | `component-builder` |

If the layers depend on each other — the usual case for a vertical slice — the fan-out
costs a multiple of the tokens to produce a handoff you then have to reconcile. Build
it yourself.

## Mutation proof — required before VERIFY

Before entering VERIFY, prove the tests can fail for the right reason:

1. Pick the line that implements the new behaviour.
2. Revert it (do not commit).
3. Re-run the test that claims to cover it — it **must** go red, and the failure must
   name the behaviour.
4. Restore the line with `cp` from a snapshot you took first. **Never** restore with
   `git checkout -- <file>` — that reverts to HEAD and destroys every uncommitted
   change in the file, including work that was never part of the mutation.
5. Re-run — green again.

A test that passes with and without the change is decoration. Record which line you
mutated and which test went red; VERIFY and the PR both want it.

## Phases

| # | Phase  | What happens                                             | Details                                     |
|---|--------|----------------------------------------------------------|---------------------------------------------|
| 1 | SPEC   | Types, API contract, acceptance criteria from the slice  | [phases/01-spec.md](phases/01-spec.md)      |
| 2 | BUILD  | Tests + implementation together (or `--layers` fan-out)  | [phases/02-build.md](phases/02-build.md)    |
| 3 | WIRE   | Connect layers, add routing                              | [phases/03-wire.md](phases/03-wire.md)      |
| 4 | VERIFY | Parent scopes the diff, then launches the specialists    | [phases/04-verify.md](phases/04-verify.md)  |

When entering a phase, read its detail file.

## Rules

1. **Read the slice before building** — the acceptance criteria are the contract
2. **Single-agent build by default**; `--layers` only for genuinely independent surfaces
3. **Mutation proof before VERIFY** — non-negotiable
4. **Track progress** — update tasks on phase entry and completion
5. **No backward compatibility** — delete old code completely. No re-exports, no
   `_deprecated` prefixes, no `// removed` comments, no shims
6. **Never `sleep`-poll a long command** — run it with `run_in_background: true` and
   wait for the completion notification
