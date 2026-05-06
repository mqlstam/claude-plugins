# Phase 4: VERIFY (Parallel)

## Goal
Independent, parallel verification across 5 specialists. Each runs in the same worktree as the parent (no `isolation: "worktree"` flag — known broken when nested, see Claude Code issues #47548 #27881 #50850 #39886 #41010). Specialists fan out, return results, parent decides ship vs fix.

## Why parallel + multi-specialist

Single-reviewer sequential pattern (the previous Phase 4 + Phase 5) leaves 30-40% of bug-detection on the table per CodeX-Verify (arxiv 2511.16708). Mixing models (Sonnet + Opus + Haiku) neutralizes the documented "self-affirming review" failure where a same-model verifier misses what its own family wrote (arxiv 2504.03846). Meta JIT testing (4× bug detection) requires writing a new diff-aware spec per slice — that's the `jit-e2e-author` lane. The total fan-out runs in ~6-10 min vs the sequential ~20 min.

## Agents

Launch ALL FIVE in a single message. No `isolation: "worktree"` flag. They share the parent worktree CWD by design.

| Agent | Model | When it does work | When it no-ops |
|-------|-------|-------------------|----------------|
| `code-quality-reviewer` | sonnet | always — markers, type safety, dead code, layer boundaries | never |
| `invariant-runner` | haiku | always — runs project's `pnpm check:*`, lint, typecheck | never |
| `jit-e2e-author` | opus | diff touches user-facing surface (routes, components, API) | backend-only / pure utils |
| `rehydration-checker` | opus | diff touches chat persistence or rendering | non-chat slices |
| `temporal-checker` | haiku | diff touches Temporal workflow files | non-temporal slices |

3 of 5 are scope-gated and will no-op on most slices, keeping steady-state cost low (~$0.30-1.00 per VERIFY run depending on diff size).

Cross-slice invariants are enforced deterministically via the consuming repo's `pnpm check:*` scripts and `tooling/eslint-plugin-*` rules; the `invariant-runner` specialist runs whichever exist. Repos that need the pattern can copy `.claude/contracts.md` from a reference project (Endoxia's is the canonical example).

## Steps

1. Confirm dev server is reachable on `E2E_BASE_URL` (default `http://localhost:3000`) — required for `jit-e2e-author`. If down, ask the user to start it (`pnpm dev:up` or equivalent) before continuing.

2. Cross-chat runtime lock — if you have multiple chats running VERIFY simultaneously, the runtime lane must serialize on `.claude/verify-runtime.lock`. Before launching `jit-e2e-author`:

   ```bash
   if [ -f .claude/verify-runtime.lock ]; then
     echo "blocked by $(cat .claude/verify-runtime.lock), waiting up to 10 min"
     # poll every 30s, give up at 600s
   fi
   echo "$(pwd)" > .claude/verify-runtime.lock
   ```

   Release after `jit-e2e-author` returns: `rm .claude/verify-runtime.lock`. Static lanes never touch the lock.

3. Launch all five specialists in one message. Each is single-shot (one Agent invocation, returns once). They cannot ask the user questions mid-run — their inputs are deterministic (the diff + project rules).

4. Collect verdicts:
   - `code-quality-reviewer` → READY | NEEDS_ATTENTION | NEEDS_WORK
   - `invariant-runner` → ALL_PASS | FAIL | DEGRADED
   - `jit-e2e-author` → READY | NEEDS_ATTENTION | NO_OP | BLOCKED
   - `rehydration-checker` → READY | NEEDS_ATTENTION | BLOCKED | NO_OP
   - `temporal-checker` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP

5. Aggregate to one verdict for the parent agent:
   - **READY** — every active specialist returned READY/ALL_PASS/NO_OP
   - **NEEDS_FIX** — any specialist returned NEEDS_WORK / FAIL / NEEDS_ATTENTION on a non-trivial issue
   - **BLOCKED** — runtime lock or dev server unreachable; surface to user, don't ship

## After all return

- **READY** → write `.claude/.verify-state.json` with current `git rev-parse HEAD` and timestamp. Suggest `/ship` to the user.
- **NEEDS_FIX** → main agent (you, in this chat) reads each specialist's report, fixes in this chat, then re-runs only the failing specialist (not the whole fan-out).
- **BLOCKED** → surface to user. Do not ship.

## Completion

- All five specialists returned
- Aggregated verdict produced
- `.claude/.verify-state.json` written if READY
- Mark verify task as completed
- Suggest `/ship` if READY
