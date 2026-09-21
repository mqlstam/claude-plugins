# Phase 5: VERIFY (Parallel)

## Goal
Independent, parallel verification. **You (the parent) decide which specialists run,
from the diff, before launching anything.** Refactor's verify carries one specialist
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
| `invariant-runner`      | haiku  | **always**                                                                                                       |
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

There is no `.claude/verify-runtime.lock` any more: each worktree runs its own private
stack. The machine-wide `gate-lock` covers the only remaining contention — the heavy gate.

## Step 3 — launch, in ONE message

`invariant-runner` records what passed against the content key (commit SHA **plus** a
tree hash covering uncommitted and untracked changes), which is what lets `/ship` and
`.husky/pre-push` skip exactly those commands and nothing else.

**Do not `sleep`-poll.** Specialists return on their own; a backgrounded gate notifies
on completion. Polling was 46% of measured tool wall-clock across 47 ships.

## Step 4 — collect verdicts

- `code-quality-reviewer` → READY | NEEDS_ATTENTION | NEEDS_WORK
- `invariant-runner` → ALL_PASS | FAIL | DEGRADED
- `dead-code-finder` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP
- `jit-e2e-author` → READY | NEEDS_ATTENTION | NO_OP | BLOCKED | NOT_RUN
- `rehydration-checker` → READY | NEEDS_ATTENTION | BLOCKED | NO_OP
- `temporal-checker` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP

## Step 5 — aggregate

- **READY** — every launched specialist returned READY/ALL_PASS/NO_OP
- **NEEDS_FIX** — any returned NEEDS_WORK / FAIL / NEEDS_ATTENTION on a non-trivial issue
- **BLOCKED** — a specialist could not do its job. Surface it.

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
- **NEEDS_FIX** → fix in this chat, then re-run **only** the failing specialist. A fix
  changes the tree, so every recorded gate green is discarded and `/ship` re-runs those
  commands — which is correct.
- **BLOCKED** → surface to the user. Do not ship.

## Completion

- Every in-scope specialist returned, and every skipped one was named with its reason
- Aggregated verdict produced
- `.claude/.verify-state.json` written if READY
- Mark verify task as completed; suggest `/ship` if READY
