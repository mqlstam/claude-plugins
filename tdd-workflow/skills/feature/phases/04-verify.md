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

**Wave 2, once wave 1 has returned and its fixes are in: run the gate yourself, once,
on that final tree, with `run_in_background: true`.**

- The repo's `package.json` has `ship:gate`: `pnpm ship:gate --label verify`, with the
  Bash `timeout` at `7200000` (a full gate may hold for 75 minutes, past the 30-minute
  default), and the additions the diff needs with one `--reason` for them all — the
  same judgment as `/ship` step 4
  (what no import reaches: a contract crossed over HTTP or the database, text loaded
  at runtime, `--full` for a change whose reach you cannot bound). It records the tree
  it proved, machine-wide, and `/ship`, `pre-push` and the land queue reuse that
  record for the identical tree: that is why a `/ship` after a green VERIFY is fast.
- Otherwise: the repo's own lint, typecheck and test commands (`pnpm lint`,
  `pnpm typecheck`, `pnpm test`, or what its `package.json` names), each its own
  background call.

Record nothing else; the gate keeps its own record.

Why the order (measured on two consecutive ships, 2026-10-07): launched together, the
reviewer edited files while the gate was running both times. Every edit moved the
tree, the gate's record stopped covering it, and the gate ran again. The gate is the
only step keyed to the exact tree, so it runs on the tree the judges leave behind.

**Do not `sleep`-poll while they work.** Specialists return on their own; the
backgrounded gate notifies on completion. Polling was 46% of measured tool wall-clock.

## Step 4 — collect verdicts

- `code-quality-reviewer` → READY | NEEDS_ATTENTION | NEEDS_WORK
- `jit-e2e-author` → READY | NEEDS_ATTENTION | NO_OP | BLOCKED | NOT_RUN
- `rehydration-checker` → READY | NEEDS_ATTENTION | BLOCKED | NO_OP
- `temporal-checker` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP
- the gate → its exit: `0` pass; `1` fail (it names what failed); `2` refused — the
  environment, not the code: do what its message says and run it again; `75` no slot
  came free before its deadline: run it again

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
  and timestamp. Suggest `/ship`.
- **NEEDS_FIX** → fix in this chat, re-run **only** the failing judge, then the gate
  (wave 2) once more on the fixed tree.
- **BLOCKED** → surface to the user. Do not ship.

## Completion

- Every in-scope specialist returned, and every skipped one was named with its reason
- The gate ran on the final tree
- Aggregated verdict produced
- `.claude/.verify-state.json` written if READY
- Mark verify task as completed; suggest `/ship` if READY
