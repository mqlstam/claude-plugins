---
name: ship
description: Validate, commit, push, and create PR for current work
disable-model-invocation: true
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`
- Recent commits on this branch: !`git log --oneline -5`

## How to run the gate (read this before step 3)

Measured over 47 ships: median invoke→merge **36 min**, p90 **140 min**, and **46%
of tool wall-clock was `sleep`/poll loops** around a gate that exceeds the 10-minute
`Bash` ceiling and gets backgrounded. Two rules, both non-negotiable:

1. **Split the gate into calls that FIT under the ceiling.** Never one
   `lint typecheck test build && every check` mega-command. The split below is also
   what makes each piece separately reusable by the record.
2. **Anything that might exceed ~8 minutes runs with `run_in_background: true`, and
   you WAIT FOR THE COMPLETION NOTIFICATION.** Do not write a `sleep 60`/poll loop.
   Do not re-run a command "to see where it is". While a background call runs, do
   something useful in the foreground (read the diff, draft the PR body) or simply
   wait.

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

3. **Ask what already passed, and say what you are skipping.**

   VERIFY's `invariant-runner` records every command it ran green against a content
   key — the commit SHA **plus a tree hash covering uncommitted and untracked
   changes**. If nothing has been touched since, those commands do not run again.

   ```bash
   node scripts/gate-record.mjs covered | sed 's/^/  reused from VERIFY: /'
   ```

   Print that list. **Never skip silently** — a reader must be able to see which
   commands ran in this `/ship` and which were inherited. If the list is empty (the
   tree moved since VERIFY, or VERIFY did not record), everything below runs, which
   is the correct outcome, not a failure.

   If the repo has no `scripts/gate-record.mjs`, treat every command as uncovered
   and run the gate in full.

4. **The turbo lane — two bounded calls, under the machine-wide lock.**

   Only one heavy gate runs at a time across every worktree on this machine. A
   `/ship` that normally takes ~36 min took **238 minutes** at load average 126, with
   turbo tasks dying on SIGINT rather than failing — killed under contention, not
   red. `gate-lock run` waits visibly, names the holder, and releases on interrupt.

   **Anchor the affected set to `origin/main`, and include `build`.** The remote
   deploy validates against `origin/main` over a full checkout and runs the
   production build. A local `--affected` computed against a stale/diverged local
   `main` — or one that omits `build` — tests a NARROWER set than what actually
   lands, so local-green ≠ deploy-green (the #1 cause of a `/ship`-green commit
   failing at deploy).

   Run these in a **plain shell, never through `scripts/wt.sh`** — the worktree
   exports break a fake-timer unit test in core and turn the provisioning contract
   into an RLS 42501.

   ```bash
   git fetch --quiet origin main 2>/dev/null || true

   # 4a — fast (~45 s cold, ~0.3 s warm). Foreground.
   node scripts/gate-lock.mjs run --label "ship lint+typecheck" -- \
     env TURBO_SCM_BASE=origin/main pnpm exec turbo run lint typecheck --affected \
   && node scripts/gate-record.mjs record turbo:lint-typecheck pass --by ship
   ```

   ```bash
   # 4b — the long one. run_in_background: true, then WAIT for the notification.
   #      --concurrency=1: measured, tasks get SIGINT-killed under contention and a
   #      web suite times out at 30 s that passes in 4 s alone.
   #      --log-order=stream: grouped output buffers a whole task and makes a running
   #      suite look like a hang (the 48-minute "CI hang" of 2026-09-13 was this).
   node scripts/gate-lock.mjs run --label "ship test+build" -- \
     env TURBO_SCM_BASE=origin/main pnpm exec turbo run test build --affected \
       --concurrency=1 --log-order=stream \
   && node scripts/gate-record.mjs record turbo:test-build pass --by ship
   ```

   Skip 4a and/or 4b if step 3 already listed them. STOP on failure — and do NOT
   accept a `FULL TURBO` (all-cached) re-run as proof after a failure; it re-proves
   nothing. Use `--force`.

   Non-Turborepo repos: `pnpm lint`, `pnpm typecheck`, `pnpm build`, and
   `pnpm test --changed origin/main` (else `pnpm test`), same split, same lock.

   ```bash
   # 4c — formatting. lint-staged covers the commit path; this catches amends and
   #      out-of-scope adds. Run it BEFORE the commit so lint-staged has nothing to
   #      rewrite — a hook that reformats on the way in invalidates the gate record.
   pnpm format:check && node scripts/gate-record.mjs record format:check pass --by ship
   ```

5. **Project invariants — one parallel run, not ~66 serial cold starts.**

   ```bash
   pnpm gate:checks
   ```

   `scripts/run-checks-parallel.mjs` discovers every `check:*` / `env:*` script from
   `package.json`, decomposes the `check:contracts` aggregate (proving every member
   is accounted for rather than trusting it), runs them in three lanes (light ×
   cores−1, heavy × 1, db × 1), streams a pass/fail table, keeps each failure's full
   output under `.claude/gate-out/`, records each pass, and exits non-zero on any
   failure. It excludes the `:update` variants — an updater OVERWRITES the committed
   baseline with whatever the tree measures, so it cannot fail; running one here
   reports green while redefining the target, including drift that came from `main`.

   It refuses to start, with the fix, when it is run under a worktree shell or when a
   prerequisite build is missing (`@endoxia/shared` dist, `packages/web/.next`) —
   both produce red results that are about the checkout rather than the code.

   If the repo has no `gate:checks`, fall back to the serial loop, still excluding
   the updaters:

   ```bash
   for script in $(jq -r '.scripts | keys[]
                          | select(test("^check:|^env:|env-drift"))
                          | select(test(":update$") | not)' package.json); do
     pnpm "$script" || { echo "FAILED: pnpm $script"; exit 1; }
   done
   ```

   If an assertion legitimately needs a new baseline, update it DELIBERATELY as part
   of the change — ideally by hand-editing only the entries your diff moved — and say
   so in the PR. Never as a side effect of the pre-ship sweep.

These local checks are the primary safety net when remote CI workflows are disabled.
Treat any failure as blocking.

## Ship

Steps 1-3 in a SINGLE message; then the PR; then the merge.

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
3. **Promote the gate record onto the new commit, then push:**

   ```bash
   node scripts/gate-record.mjs promote
   git push -u origin <branch>
   ```

   `promote` re-keys the record onto the commit you just made — but only after
   recomputing the tree hash and finding it unchanged. If `git add` staged only part
   of the tree, or a `pre-commit` hook reformatted a file on the way in, it refuses,
   says why, and `pre-push` then runs in full. That is the mechanism that makes the
   hook's skip **verifiable**: an environment variable would let anything silence it;
   a tree hash cannot be asserted, only recomputed.

   Expect `pre-push` to print `reused from the gate that ran on this commit` for each
   of its sections. If it runs them instead, read its reason — something moved.

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

## After the merge — reclaim, then hand back

1. **Stop what this worktree started, in this order: dev server, Chrome, containers.**
   Stop BEFORE deleting build output — `rm -rf packages/web/.next` under a live
   `next dev` corrupts the server's state, which is the wipe lane D's `turbo.json`
   change (2026-09-18) exists to prevent. Each entry point is pidfile-scoped and
   idempotent, so calling it when nothing runs is a no-op.

   ```bash
   bash scripts/wt.sh ./scripts/worktree-dev-detached.sh --stop 2>/dev/null || true
   bash scripts/wt.sh ./scripts/worktree-chrome.sh --stop 2>/dev/null || true
   bash scripts/wt.sh docker compose stop 2>/dev/null || true
   ```

2. **Delete only `packages/web/.next`.** A stale `.next` has skewed a bundle budget and
   filled the disk on this machine. Do NOT delete `packages/*/dist`: `pnpm env:drift`
   and `check:contracts` read `packages/shared/dist`, and `scripts/run-checks-parallel.mjs`
   refuses to start without it — a worktree stripped of `dist` cannot run its own next
   gate. The SessionStart sweep (`scripts/worktree-sweep.mjs`) prices this the same way
   and prunes `.next` only.

   ```bash
   rm -rf packages/web/.next
   ```

3. **PRINT the removal commands. Do not run them.** The session is running INSIDE
   this worktree; removing it from here pulls the floor out. Print them only when the
   branch is merged and the tree is clean, and let the user run them from another
   terminal:

   ```bash
   branch=$(git branch --show-current)
   wt=$(git rev-parse --show-toplevel)
   if [ -z "$(git status --porcelain)" ] && git branch --merged origin/main | grep -qx "  $branch"; then
     cat <<EOF

   Merged and clean. To reclaim this worktree, from ANOTHER terminal:

     git -C "$(git rev-parse --git-common-dir)/.." worktree remove "$wt"
     git -C "$(git rev-parse --git-common-dir)/.." branch -d "$branch"

   (The next session start also sweeps stacks whose worktree directory is gone.)
   EOF
   fi
   ```

**Merging does NOT deploy to production.** Deploy is decoupled: it fires only on a
`deploy-*` tag (or manual dispatch), not on a push to `main`. `/ship` lands your work
on `main`; nothing reaches a box until you explicitly run **`/deploy`**, and where a
project has a staging step (Endoxia, slice 603) that tag reaches STAGING only —
production is a separate **`/promote`**. This is intentional — merge freely, release
deliberately.
