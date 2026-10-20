# Phase 4: VERIFY (Parallel)

## Goal
Independent, parallel verification. **You (the parent) decide which specialists run,
from the diff, before launching anything, and you run the gate yourself once they
are done.** Specialists run in the same worktree as the parent (no
`isolation: "worktree"` flag — known broken when nested, see Claude Code issues
#47548 #27881 #50850 #39886 #41010).

## Why parallel + multi-specialist

Single-reviewer sequential review leaves 30-40% of bug detection on the table per
CodeX-Verify (arxiv 2511.16708). Mixing model families neutralizes the documented
"self-affirming review" failure (arxiv 2504.03846). Meta JIT testing (4× bug
detection) requires a new diff-aware spec per slice — that is the `jit-e2e-author`
lane.

## Step 1 — scope the diff YOURSELF, and print what you are not launching

```bash
git diff --name-only origin/main...HEAD
git diff --name-only HEAD          # uncommitted work counts too
```

Decide from those paths. Measured over three weeks: `temporal-checker` returned
`NO_OP` on **52%** of its launches and `rehydration-checker` on **42%** — half of
those launches paid a full agent start-up to read a diff and conclude they had
nothing to do. You already have the diff; deciding here costs nothing.

| Specialist              | Model  | Launch when the diff touches                                                                                     |
| ----------------------- | ------ | ---------------------------------------------------------------------------------------------------------------- |
| `code-quality-reviewer` | sonnet | **always**                                                                                                       |
| `jit-e2e-author`        | opus   | user-facing surface: `app/`, `pages/`, `*.tsx`, `middleware.ts`, API route files, auth/session code             |
| `rehydration-checker`   | opus   | chat persistence or chat-visible rendering: `chat_turns`, `UIMessage`, message-part mappers/renderers, `features/chat-*` |
| `temporal-checker`      | haiku  | `packages/core/src/workflows/`, `packages/core/src/temporal/`, activity definitions, `workflow-versioning`        |
| `dead-code-finder`      | sonnet | feature workflow: never (refactor only)                                                                          |

Print one line per specialist you skip, with the reason:

```
skipping temporal-checker  — no files under packages/core/src/{workflows,temporal}/
skipping rehydration-checker — no chat persistence or rendering paths in the diff
```

Each specialist keeps its own `NO_OP` guard as defence in depth — a path heuristic
can be wrong, and a specialist that disagrees with your scoping should say so rather
than invent work.

## Step 2 — if e2e is in scope, start the stack FIRST, in the background

`jit-e2e-author` needs a running stack, and its median run is 12.3 min. The recurring
failure was: launch it, it works for minutes, THEN discovers no stack is running and
asks for `/spinup`. So the order is fixed:

1. **Start the stack in the background** (do not block on it):
   ```bash
   bash scripts/wt.sh docker compose up -d        # per-worktree private stack
   ```
   Reference, when present in the repo: `scripts/worktree-stack-cap.sh` (refuses a
   new stack when the machine's cap is reached), `scripts/worktree-chrome.sh`, and a
   light compose profile if one is defined. Tolerate their absence — fall back to
   the plain compose up.
2. **Launch every static specialist immediately, in parallel.** They never touch the
   runtime and must not wait for it.
3. **Launch `jit-e2e-author` only once the stack answers** on `$WT_BFF_PORT` (default
   3000).
4. **If the stack cannot come up** — the cap is reached, a first-time image build
   would take longer than this round, compose errors — say so plainly and record the
   round as **e2e NOT RUN**. Do not report it as a pass, and do not let it block the
   static verdicts.

There is no `.claude/verify-runtime.lock`: each worktree runs its own private stack.

## Step 3 — the judges, then the gate

**Wave 1, in ONE message:** every in-scope specialist. Each is single-shot; they
cannot ask the user questions mid-run. They read, judge, and may fix trivial issues
in place.

**Wave 2, once wave 1 has returned and its fixes are in: run local validation
on that final tree yourself.** Read this checkout's own `package.json`. With a
`ship:check` script run `pnpm ship:check`, adding `--add-tests`, `--add-package`
or `--add-checks` only with `--reason` for a boundary automatic selection misses.
Without it follow `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`, including its
assertion-only sweep. Never narrow the selected checks.

Commands that can exceed about 8 minutes use `run_in_background: true`; wait for
the completion notification. Do not sleep-poll. Specialists may edit the tree, so
validation runs after their final fixes. Any new fixes require validation again.

## Step 4 — collect verdicts

- `code-quality-reviewer` → READY | NEEDS_ATTENTION | NEEDS_WORK
- `jit-e2e-author` → READY | NEEDS_ATTENTION | NO_OP | BLOCKED | NOT_RUN
- `rehydration-checker` → READY | NEEDS_ATTENTION | BLOCKED | NO_OP
- `temporal-checker` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP
- local validation → `0` pass; `1` fail (names the failed step); `2` refused:
  fix the named environment issue and retry. An interrupted command or any other
  nonzero exit is not a pass. Surface a cause you cannot resolve as BLOCKED.

## Step 5 — aggregate

- **READY** — every launched specialist returned READY/NO_OP and the gate passed
- **NEEDS_FIX** — any returned NEEDS_WORK / NEEDS_ATTENTION on a non-trivial issue, or
  the gate failed
- **BLOCKED** — a specialist could not do its job (not the same as NOT_RUN), or the
  gate refused for a reason you cannot fix here. Surface it.

`NOT_RUN` for e2e does not block READY, but it MUST appear in the summary you give
the user. A verification that did not happen is reported, never absorbed.

## After all return

- **READY** → write `.claude/.verify-state.json` with the current `git rev-parse HEAD`
  and timestamp. `/ship` checks this before merging main or committing, so those
  integration steps do not re-arm VERIFY. Suggest `/ship`.
- **NEEDS_FIX** → fix in this chat, re-run **only** the failing judge, then the gate
  (wave 2) once more on the fixed tree.
- **BLOCKED** → surface to the user. Do not ship.

## Completion

- Every in-scope specialist returned, and every skipped one was named with its reason
- The gate ran on the final tree
- Aggregated verdict produced
- `.claude/.verify-state.json` written if READY
- Mark verify task as completed; suggest `/ship` if READY
