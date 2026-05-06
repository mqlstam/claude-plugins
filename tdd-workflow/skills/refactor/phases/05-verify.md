# Phase 5: VERIFY (Parallel)

## Goal
Independent, parallel verification across 6 specialists. Each runs in the same worktree as the parent (no `isolation: "worktree"` flag — known broken when nested, see Claude Code issues #47548 #27881 #50850 #39886 #41010). Refactor's verify is one specialist heavier than feature's because we additionally need to confirm the old implementation was actually deleted.

## Why parallel + multi-specialist

Single-reviewer sequential pattern (the previous Phase 5 + Phase 6) leaves 30-40% of bug-detection on the table per CodeX-Verify (arxiv 2511.16708). Mixing models (Sonnet + Opus + Haiku) neutralizes the "self-affirming review" failure where a same-model verifier misses what its own family wrote (arxiv 2504.03846). For refactors specifically, the dead-code finder enforces the project's "no backwards-compat shims" rule — refactors that leave shims behind are the most common refactor regression.

## Agents

Launch ALL SIX in a single message. No `isolation: "worktree"` flag. They share the parent worktree CWD by design.

| Agent | Model | When it does work | When it no-ops |
|-------|-------|-------------------|----------------|
| `code-quality-reviewer` | sonnet | always — markers, type safety, dead code, layer boundaries | never |
| `invariant-runner` | haiku | always — runs project's `pnpm check:*`, lint, typecheck | never |
| `jit-e2e-author` | opus | diff touches user-facing surface (routes, components, API) | backend-only / pure utils |
| `rehydration-checker` | opus | diff touches chat persistence or rendering | non-chat slices |
| `temporal-checker` | haiku | diff touches Temporal workflow files | non-temporal slices |
| `dead-code-finder` | sonnet | refactor renamed/moved/deleted symbols (almost always) | trivial rename with all callers cleanly updated |

Cross-slice invariants are enforced deterministically via the consuming repo's `pnpm check:*` scripts and `tooling/eslint-plugin-*` rules; the `invariant-runner` specialist runs whichever exist. Repos that need the pattern can copy `.claude/contracts.md` from a reference project (Endoxia's is the canonical example).

## Steps

1. Confirm dev server is reachable on `E2E_BASE_URL` (default `http://localhost:3000`) — required for `jit-e2e-author`. If down, ask the user to start it before continuing.

2. Cross-chat runtime lock — if you have multiple chats running VERIFY simultaneously, the runtime lane must serialize on `.claude/verify-runtime.lock`:

   ```bash
   if [ -f .claude/verify-runtime.lock ]; then
     echo "blocked by $(cat .claude/verify-runtime.lock), waiting up to 10 min"
   fi
   echo "$(pwd)" > .claude/verify-runtime.lock
   # release after jit-e2e-author returns
   ```

   Static lanes never touch the lock.

3. Launch all six specialists in one message.

4. Collect verdicts:
   - `code-quality-reviewer` → READY | NEEDS_ATTENTION | NEEDS_WORK
   - `invariant-runner` → ALL_PASS | FAIL | DEGRADED
   - `jit-e2e-author` → READY | NEEDS_ATTENTION | NO_OP | BLOCKED
   - `rehydration-checker` → READY | NEEDS_ATTENTION | BLOCKED | NO_OP
   - `temporal-checker` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP
   - `dead-code-finder` → READY | NEEDS_ATTENTION | NEEDS_WORK | NO_OP

5. Aggregate to one verdict:
   - **READY** — every active specialist returned READY/ALL_PASS/NO_OP
   - **NEEDS_FIX** — any specialist returned NEEDS_WORK / FAIL / NEEDS_ATTENTION on a non-trivial issue
   - **BLOCKED** — runtime lock or dev server unreachable

## Refactor-specific failure modes the dead-code finder catches

These are the most common refactor regressions and they're invisible to code-quality-reviewer because the new code looks fine — the bug is what wasn't deleted:

- Old export still re-exported from new location ("compatibility")
- `_deprecated` prefix on retained-but-unused names
- Stale `// removed` / `// kept just in case` comments
- Files renamed but with substantial unchanged content (zombies)
- Stale references to renamed symbols somewhere callers weren't migrated

These violate the project's "no backwards-compat shims" rule (per CLAUDE.md in most projects). They turn a clean refactor into accumulating debt over time.

## After all return

- **READY** → write `.claude/.verify-state.json` with current `git rev-parse HEAD` and timestamp. Suggest `/ship` to the user.
- **NEEDS_FIX** → main agent reads each specialist's report, fixes in this chat, then re-runs only the failing specialist.
- **BLOCKED** → surface to user. Do not ship.

## Completion

- All six specialists returned
- Aggregated verdict produced
- `.claude/.verify-state.json` written if READY
- Mark verify task as completed
- Suggest `/ship` if READY
