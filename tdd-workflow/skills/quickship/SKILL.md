---
name: quickship
description: Validate and push directly to main without a PR. Same gates as /ship — only the merge mechanism differs.
disable-model-invocation: true
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(npm *), Bash(pnpm *), Bash(node *), Bash(jq *)
---

# Quick Ship

Pushes straight to main. Skips the PR + auto-merge dance that `/ship` runs.
Use only when CI is disabled, branch protection is unconfigured, and the
audit trail of a PR is not required. For most agentic-development setups,
prefer `/ship` — pushing agent-generated code direct to main without a PR
is the documented highest-risk pattern.

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`
- Worktree: !`git rev-parse --show-toplevel`
- Primary worktree: !`git worktree list --porcelain | head -2 | tail -1 | awk '{print $2}'`

## How to run the gate

Identical to `/ship`, and for the same measured reason: split the gate into calls
that fit under the 10-minute `Bash` ceiling, run the long one with
`run_in_background: true`, and **wait for the completion notification** — never a
`sleep N` poll loop (46% of measured tool wall-clock across 47 ships).

## Validation

Based on the context above:

1. **Must be on main, in the primary worktree.** This skill commits directly to
   main; switching branches from a worktree is blocked by most projects'
   destructive-git hooks. If `Branch` ≠ `main` or `Worktree` ≠ `Primary worktree`,
   STOP with: "Use /ship from a feature branch or worktree; /quickship only runs
   from the main checkout."

2. **No changes** — if there are no changes to commit, STOP.

3. **VERIFY-state gate** — same as /ship. The parent skill's VERIFY phase must
   have run on the current diff:

   ```bash
   if [ ! -f .claude/.verify-state.json ]; then
     echo "VERIFY phase has not run. Complete it (re-enter /feature or /refactor) before /quickship."
     exit 1
   fi
   verified_sha=$(jq -r .headSha .claude/.verify-state.json 2>/dev/null || cat .claude/.verify-state.json)
   if [ "$verified_sha" != "$(git rev-parse HEAD)" ]; then
     echo "VERIFY-state is stale ($verified_sha != HEAD). Re-run VERIFY before /quickship."
     exit 1
   fi
   ```

4. **Ask what already passed, and say what you are skipping.**

   ```bash
   node scripts/gate-record.mjs covered | sed 's/^/  reused from VERIFY: /'
   ```

   VERIFY's `invariant-runner` records each green against a content key (commit SHA
   **plus** a tree hash covering uncommitted and untracked changes), so a command is
   skipped only when nothing has moved. Print the list — never skip silently. An
   empty list means everything below runs, which is correct, not a failure.

5. **The turbo lane — two bounded calls, under the machine-wide gate lock** (only one
   heavy gate at a time across every worktree; measured: a 238-minute ship at load
   average 126 with turbo tasks dying on SIGINT rather than failing).

   **Anchor the affected set to `origin/main`, and include `build`.** The remote
   deploy validates against `origin/main` over a full checkout and runs the
   production build; a local `--affected` computed against a stale/diverged local
   `main` — or one that omits `build` — tests a NARROWER set than what actually
   lands.

   Run in a plain shell, never through `scripts/wt.sh`.

   ```bash
   git fetch --quiet origin main 2>/dev/null || true

   # fast — foreground
   node scripts/gate-lock.mjs run --label "quickship lint+typecheck" -- \
     env TURBO_SCM_BASE=origin/main pnpm exec turbo run lint typecheck --affected \
   && node scripts/gate-record.mjs record turbo:lint-typecheck pass --by quickship
   ```

   ```bash
   # long — run_in_background: true, then wait for the notification
   node scripts/gate-lock.mjs run --label "quickship test+build" -- \
     env TURBO_SCM_BASE=origin/main pnpm exec turbo run test build --affected \
       --concurrency=1 --log-order=stream \
   && node scripts/gate-record.mjs record turbo:test-build pass --by quickship
   ```

   ```bash
   # formatting — before the commit, so lint-staged has nothing to rewrite
   pnpm format:check && node scripts/gate-record.mjs record format:check pass --by quickship
   ```

   Skip whichever step 4 already listed. STOP on failure; never accept a `FULL TURBO`
   all-cached re-run as proof after a failure — use `--force`.

6. **Project invariants — one parallel run:**

   ```bash
   pnpm gate:checks
   ```

   It discovers every `check:*` / `env:*` script, decomposes the aggregate rather
   than trusting it, runs three lanes (light × cores−1, heavy × 1, db × 1), preserves
   each failure's output, and records each pass. It excludes the `:update` variants:
   an updater OVERWRITES the committed baseline with whatever the tree measures, so
   it cannot fail — running it here reports green while silently redefining the
   target, absorbing drift that came from `main` rather than from your change.

   Fallback for a repo without `gate:checks`:

   ```bash
   for script in $(jq -r '.scripts | keys[]
                          | select(test("^check:|^env:|env-drift"))
                          | select(test(":update$") | not)' package.json); do
     pnpm "$script" || { echo "FAILED: pnpm $script"; exit 1; }
   done
   ```

   A baseline that genuinely needs to move is part of the change, not a side effect
   of the pre-ship sweep — update it deliberately and say so in the commit.

## Ship (do everything below in a SINGLE message)

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
   - If $ARGUMENTS provided, use as commit message
3. `node scripts/gate-record.mjs promote` — re-keys the record onto the new commit,
   but only after recomputing the tree hash and finding it unchanged. If it refuses,
   `pre-push` runs in full, which is the correct outcome.
4. Push to main: `git push origin main`

Do all of the above in a single response. Do not send any other text besides
the tool calls.

## After the push

```bash
rm -rf packages/web/.next
```

`/quickship` runs from the primary checkout, so there is no worktree to reclaim.

**Delete only `.next` — never `packages/*/dist`.** Same rule as `/ship`, and the
primary checkout is not an exception to it: `pnpm env:drift` and `check:contracts`
read `packages/shared/dist`, and `scripts/run-checks-parallel.mjs` refuses to start
without it. A checkout stripped of `dist` cannot run its own next gate, and the
failure reads as a red gate rather than as a missing build — which is worse than
the disk it frees.
