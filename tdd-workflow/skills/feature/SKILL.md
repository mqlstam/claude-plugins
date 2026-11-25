---
name: feature
description: >-
  Start a feature workflow. Default is a single-agent build against the slice's
  acceptance criteria, proved by a run on the real surface; a test only when the brief
  asks or the fault has a cheap local target; then VERIFY. Pass --layers to fan out per-layer builders. Use when the user
  wants to build a new feature, add functionality, or implement a vertical slice.
argument-hint: <feature-name> [--layers]
---

# Feature: $ARGUMENTS

## Setup

**Workspace:** Stay on the current branch — never `git checkout`, `git switch`, `git checkout -b`, `git stash`, or create branches. The user picks the workspace (worktree, feature branch, or main) at session start; the skill must respect that choice.

1. **Read the slice doc** (`docs/slices/{NN}-{name}.md` or the equivalent) for scope
   and acceptance criteria. The acceptance criteria are the contract; without them
   you are guessing at it.
2. Create tasks for tracking — one per phase.
3. **Read the real docs before building**, for every library, SDK, provider API or tool
   the slice touches:
   a. Find the version this project runs (lockfile, Dockerfile pins, compose image tags).
   b. Read its documentation for THAT version: the guides that explain the concept, show
      worked examples and set out the options, not only type definitions (they confirm a
      name; they do not say what a setting does or what leaving it out does). Look first
      for docs shipped inside the installed package; otherwise the docs site, after
      checking which version it documents. A site a major ahead means finding our
      version's path, or the repository's docs at our version's tag.
   c. Read pages as raw text: the `.md` twin, the source on GitHub, or
      `curl -H 'Accept: text/markdown'`. WebFetch returns a summary, and a summary drops
      the defaults and fallbacks that decide a design.
   d. For a provider (an LLM host, an external API), read the SDK's page for it AND the
      provider's own guide for the feature. What the provider actually does is settled
      by a live probe, not by either page.
   e. If the project has its own docs rule (e.g. `.claude/rules/library-docs.md`), follow
      it: it names the traps of that stack.
   f. Write in the SPEC, per technology: the version and the guides you read, and what
      you chose differently because of them. This applies equally to **modifying
      existing code**: the pattern you are about to extend may be one an older version
      needed.

## Build shape — single agent by default

**Default: ONE agent (you) builds the slice**, working through its acceptance
criteria. Not a red-green ceremony per layer, and not a fan-out of per-layer builders.

This is a measured position, not a preference. A controlled study plus this user's own
session data (**8-10× the token volume, 46 sub-agents in a session against 1**) found
no quality gain from rigid red-green sequencing or from per-layer builder agents. What
does earn its cost is **a run on the real surface** and **VERIFY**; a test is optional
("The bar, and which tests", below).

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

## Phases

| # | Phase  | What happens                                             | Details                                     |
|---|--------|----------------------------------------------------------|---------------------------------------------|
| 1 | SPEC   | Types, API contract, acceptance criteria from the slice  | [phases/01-spec.md](phases/01-spec.md)      |
| 2 | BUILD  | The implementation, run on the real surface              | [phases/02-build.md](phases/02-build.md)    |
| 3 | WIRE   | Connect layers, add routing                              | [phases/03-wire.md](phases/03-wire.md)      |
| 4 | VERIFY | Independent review + runtime check, beside local checks  | [phases/04-verify.md](phases/04-verify.md)  |

When entering a phase, read its detail file.

## Rules

1. **Read the slice before building** — the acceptance criteria are the contract
2. **Single-agent build by default**; `--layers` only for genuinely independent surfaces
3. **Track progress** — update tasks on phase entry and completion
4. **No backward compatibility** — delete old code completely. No re-exports, no
   `_deprecated` prefixes, no `// removed` comments, no shims
5. **Never `sleep`-poll a long command** — run it with `run_in_background: true`;
   its completion wakes you, so carry the workflow on from it in that turn. Never end the turn waiting on something with no local task (a GitHub run, a
   remote job): wrap that wait in a local background command that exits on its terminal
   state (the plugin context's "A background step is yours" rule)
