# Phase 5: VERIFY (Parallel)

## Goal
Independent, parallel verification. **You (the parent) decide which specialists run,
from the diff, before launching anything, and you run the gate yourself once they
are done.** Refactor's verify carries one specialist
feature's does not: `dead-code-finder`, because the most common refactor regression is
not in the new code — it is what was not deleted. Specialists run in the same worktree
as the parent (no `isolation: "worktree"` flag — known broken when nested, see Claude
Code issues #47548 #27881 #50850 #39886 #41010).

## Why parallel + multi-specialist

Single-reviewer sequential review leaves 30-40% of bug detection on the table per
CodeX-Verify (arxiv 2511.16708). Mixing model families neutralizes the "self-affirming
review" failure (arxiv 2504.03846).

## Step 1 — scope the diff YOURSELF, and print what you are not launching

```bash
git diff --name-only origin/main...HEAD
git diff --name-only HEAD          # uncommitted work counts too
```

Measured over three weeks: `temporal-checker` returned `NO_OP` on **52%** of its
launches and `rehydration-checker` on **42%** — half of those launches paid a full
agent start-up to read a diff and conclude they had nothing to do.

| Specialist              | Model  | Launch when the diff touches                                                                                     |
| ----------------------- | ------ | ---------------------------------------------------------------------------------------------------------------- |
| `code-quality-reviewer` | sonnet | **always**                                                                                                       |
| `dead-code-finder`      | sonnet | **almost always on a refactor** — any renamed, moved or deleted symbol. Skip only for a rename whose callers were all updated mechanically in one commit. |
| `jit-e2e-author`        | opus   | user-facing surface: `app/`, `pages/`, `*.tsx`, `middleware.ts`, API route files, auth/session code             |
| `rehydration-checker`   | opus   | chat persistence or chat-visible rendering: `chat_turns`, `UIMessage`, message-part mappers/renderers, `features/chat-*` |
| `temporal-checker`      | haiku  | `packages/core/src/workflows/`, `packages/core/src/temporal/`, activity definitions, `workflow-versioning`        |

Print one line per specialist you skip, with the reason. Each keeps its own `NO_OP`
guard as defence in depth.

## Step 2 — if e2e is in scope, start the stack FIRST, in the background

Same order as the feature workflow, for the same reason (`jit-e2e-author` median 12.3
min, and the recurring shape was starting it and only then discovering no stack):

1. `bash scripts/wt.sh docker compose up -d` in the background — with
   `scripts/worktree-stack-cap.sh` / `scripts/worktree-chrome.sh` and a light compose
   profile when the repo defines them; tolerate their absence.
2. Launch every static specialist immediately, in parallel — they never touch the runtime.
3. Launch `jit-e2e-author` only once the stack answers on `$WT_BFF_PORT`.
4. If the stack cannot come up, say so and record **e2e NOT RUN** — never a silent pass.

There is no `.claude/verify-runtime.lock`: each worktree runs its own private stack.

## Step 3 — the judges, then the gate

**Wave 1, in ONE message:** every in-scope specialist (`code-quality-reviewer`,
`dead-code-finder`, and whichever of the others the diff brings in). They may fix
trivial issues in place.

**Wave 2, once wave 1 has returned and its fixes are in: run the gate yourself, once,
on that final tree, with `run_in_background: true`** — `pnpm ship:gate --label verify`
with the Bash `timeout` at `7200000` (a full gate may hold for 75 minutes) and the
additions the diff needs with one `--reason` for them all, when the repo's
`package.json` has `ship:gate`; otherwise the repo's own lint, typecheck and test commands, each its
own background call. The feature workflow's VERIFY step 3 says how to choose the
additions; a refactor that moves a shared module usually warrants the consumers'
packages, or `--full`. Record nothing else: `ship:gate` records the tree it proved,
and `/ship`, `pre-push` and the land queue reuse it for the identical tree.

Launched together, a judge's edit moves the tree under a running gate and its record
no longer covers it; on two consecutive ships (2026-10-07) that cost a re-run each
time. The gate is the only step keyed to the exact tree, so it runs last. After a
NEEDS_FIX round: fix, re-run only the failing judge, then the gate once more.

**Do not `sleep`-poll.** Specialists return on their own; a backgrounded gate notifies
on completion. Polling was 46% of measured tool wall-clock across 47 ships.

## Step 4 — collect verdicts

- `code-quality-reviewer` → READY | NEEDS_ATTENTION | NEEDS_WORK
- `dead-code-finder` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP
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
- **BLOCKED** — a specialist could not do its job, or the gate refused for a reason
  you cannot fix here. Surface it.

`NOT_RUN` for e2e does not block READY but MUST appear in the summary.

## Refactor-specific failure modes the dead-code finder catches

Invisible to `code-quality-reviewer` because the new code looks fine — the bug is what
was not deleted:

- Old export still re-exported from the new location ("compatibility")
- `_deprecated` prefix on a retained-but-unused name
- Stale `// removed` / `// kept just in case` comments
- Files renamed but with substantial unchanged content (zombies)
- Callers never migrated off a renamed symbol

These violate the project's "no backwards-compat shims" rule and turn a clean refactor
into accumulating debt.

## After all return

- **READY** → write `.claude/.verify-state.json` with the current `git rev-parse HEAD`
  and timestamp. Suggest `/ship`.
- **NEEDS_FIX** → fix in this chat, re-run **only** the failing specialist, then the
  gate once more on the fixed tree.
- **BLOCKED** → surface to the user. Do not ship.

## Completion

- Every in-scope specialist returned, and every skipped one was named with its reason
- The gate ran on the final tree
- Aggregated verdict produced
- `.claude/.verify-state.json` written if READY
- Mark verify task as completed; suggest `/ship` if READY
