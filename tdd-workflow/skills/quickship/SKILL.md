---
name: quickship
description: Validate and push directly to main without a PR. Same gates as /ship — only the merge mechanism differs.
disable-model-invocation: true
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(npm *), Bash(pnpm *), Bash(jq *)
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

4. **Quality checks** (skip any not defined in package.json):
   - **Anchor the affected set to `origin/main`, and include `build`.** The remote
     deploy validates against `origin/main` over a full checkout and runs the
     production build. A local `--affected` computed against a stale/diverged local
     `main` — or one that omits `build` — tests a NARROWER set than what actually
     lands, so local-green ≠ deploy-green (the #1 cause of a `/ship`-green commit
     failing at deploy). Fetch first, pin the base, and add `build`:
     ```bash
     git fetch --quiet origin main 2>/dev/null || true
     TURBO_SCM_BASE=origin/main pnpm exec turbo run lint typecheck test build --affected
     ```
     `TURBO_SCM_BASE=origin/main` makes the affected set match "what will land on
     main" — turbo's default `--affected` base is the local `main` ref, which
     drifts (see turbo docs: a too-shallow/absent base makes turbo treat ALL
     packages as changed, the same failure the remote hit on a shallow tag
     checkout). Adding `build` runs the production build (e.g. `next build`) so
     build-only type errors and client→server import breaks — which pass
     `typecheck` + `test` — fail HERE, locally and free, not in a billed remote
     image build. STOP on failure.
   - Non-Turborepo repos: `pnpm lint`, `pnpm typecheck`, `pnpm build`, and
     `pnpm test --changed origin/main` (if supported, else `pnpm test`) — STOP on
     any failure.
   - `pnpm format:check` if defined — STOP on failure

5. **Project invariants** — discover and run every `pnpm check:*` / `pnpm env:*`
   script. STOP on any failure; surface the failing command + first 10 lines.

   ```bash
   for script in $(jq -r '.scripts | keys[] | select(test("^check:|^env:|env-drift"))' package.json); do
     pnpm "$script" || { echo "FAILED: pnpm $script"; exit 1; }
   done
   ```

## Ship (do everything below in a SINGLE message)

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
   - If $ARGUMENTS provided, use as commit message
3. Push to main: `git push origin main`

Do all of the above in a single response. Do not send any other text besides
the tool calls.
