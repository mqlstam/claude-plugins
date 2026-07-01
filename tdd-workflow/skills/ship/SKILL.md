---
name: ship
description: Validate, commit, push, and create PR for current work
disable-model-invocation: true
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`
- Recent commits on this branch: !`git log --oneline -5`

## Validation

Based on the context above:

1. **No changes** — if there are no changes to commit, STOP.

2. **VERIFY-state gate** — confirm the parent skill's VERIFY phase ran on the
   current diff. The verify phase writes `.claude/.verify-state.json` with the
   HEAD SHA at completion.

   ```bash
   if [ ! -f .claude/.verify-state.json ]; then
     echo "VERIFY phase has not run. Complete it (re-enter /feature or /refactor) before /ship."
     exit 1
   fi
   verified_sha=$(jq -r .headSha .claude/.verify-state.json 2>/dev/null || cat .claude/.verify-state.json)
   if [ "$verified_sha" != "$(git rev-parse HEAD)" ]; then
     echo "VERIFY-state is stale ($verified_sha != HEAD). Re-run VERIFY before /ship."
     exit 1
   fi
   ```

   STOP if either check fails. Do not proceed without a fresh verify-state.

3. **Quality checks** (skip any that don't exist in the project's package.json):
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

4. **Project invariants** — discover and run every `pnpm check:*` and `pnpm env:*`
   script defined in root `package.json`. These typically enforce per-project
   guardrails (RLS, ownership, env-drift, marker conventions). STOP on any
   failure; surface the exact command + first 10 lines of stderr.

   ```bash
   for script in $(jq -r '.scripts | keys[] | select(test("^check:|^env:|env-drift"))' package.json); do
     pnpm "$script" || { echo "FAILED: pnpm $script"; exit 1; }
   done
   ```

These local checks are the primary safety net when remote CI workflows are
disabled. Treat any failure as blocking — the goal is to ship green and the
goal of the VERIFY-state gate is to ensure the multi-specialist fan-out ran
on this exact diff.

## Ship (do everything below in a SINGLE message)

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
3. Push the branch: `git push -u origin <branch>`
4. Create a PR:
   - If $ARGUMENTS provided, use as title
   - Otherwise, derive title from the commit message
   - Body: summary of changes + test plan
   ```bash
   gh pr create --title "<title>" --body "..."
   ```
5. Squash-merge. **Inspect the branch-protection state first — do not blind-guess
   `--auto || --admin`** (that silently force-merges when a required check is
   stalled/absent). Pick deterministically:
   ```bash
   PR=$(gh pr view --json number -q .number)
   required=$(gh api repos/:owner/:repo/branches/main/protection/required_status_checks \
     --jq '.contexts | length' 2>/dev/null || echo 0)
   if [ "$required" = 0 ]; then
     # No required check (this repo, pre-beta) → clean squash, no --admin needed.
     gh pr merge "$PR" --squash --delete-branch
   else
     cs=$(gh pr checks "$PR" --json name,state -q '.[]|select(.name=="check")|.state' 2>/dev/null)
     case "$cs" in
       SUCCESS) gh pr merge "$PR" --squash --delete-branch ;;
       *) echo "BLOCKED: required 'check' is $cs. Local /ship already validated; to force, re-run with FORCE_ADMIN=1."
          [ "${FORCE_ADMIN:-0}" = 1 ] && gh pr merge "$PR" --squash --admin --delete-branch || exit 2 ;;
     esac
   fi
   ```
   `--admin` only ever fires behind an explicit `FORCE_ADMIN=1` — never silently.

**Merging does NOT deploy to production.** Deploy is decoupled: it fires only on a
`deploy-*` tag (or manual dispatch), not on a push to `main`. `/ship` lands your
work on `main`; nothing reaches prod until you explicitly run **`/deploy`**. This
is intentional — merge freely, ship to prod deliberately.

Do all of the above in a single response. Do not send any other text besides the tool calls.
