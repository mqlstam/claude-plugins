---
name: ship
description: Validate, commit, push, and create PR for current work. Runs when the user types /ship, or when Claude invokes it after the user typed /approve ship (one run).
release-actions: merge
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py *)
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
`Bash` ceiling and gets backgrounded. Three rules, all non-negotiable:

1. **Split the gate into calls that FIT under the ceiling.** Never one
   `lint typecheck test build && every check` mega-command. The split below is also
   what makes each piece separately reusable by the record.
2. **Anything that might exceed ~8 minutes runs with `run_in_background: true`, and
   you WAIT FOR THE COMPLETION NOTIFICATION.** Do not write a `sleep 60`/poll loop.
   Do not re-run a command "to see where it is". While a background call runs, do
   something useful in the foreground (read the diff, draft the PR body) or simply
   wait. The plugin's `bash-shape-guard` hook refuses a foreground `sleep` above
   10 s, a `sleep` inside a loop, and a `| tail` whose status is then used, in
   subagents too.
3. **Independent steps run side by side.** 4a and 4c read only the source and
   neither waits on the other: issue them in ONE message as two tool calls. 4b
   starts once 4a is green, in the background; step 5 runs after 4b, because some
   checks read the build output 4b writes.

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

   First, as its own plain command (git inside a compound command is refused in a
   worktree session): `git fetch --quiet origin main`.

   ```bash
   # 4a — fast (~45 s cold, ~0.3 s warm). Foreground, in the same message as 4c.
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
   # 4c — formatting, beside 4a (it takes no lock and reads only the source).
   #      lint-staged covers the commit path; this catches amends and out-of-scope
   #      adds. Run it BEFORE the commit so lint-staged has nothing to rewrite — a
   #      hook that reformats on the way in invalidates the gate record.
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
3. **Promote the gate record onto the new commit, then push** (two separate plain
   commands: git inside a compound command is refused in a worktree session):

   ```bash
   node scripts/gate-record.mjs promote
   ```
   ```bash
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

   **Main moved since the gate?** Check `git rev-list --count HEAD..origin/main` before
   pushing. If it is not 0, merge `origin/main` in (`git merge --no-edit origin/main`),
   resolve, commit, then `promote` again. When main only carries what this branch
   already contains (the squash of an earlier slice shipped from this same branch),
   the tree hash is unchanged and every record carries over; otherwise `promote`
   refuses and the gate re-runs on the merged tree, which is the point. A slice index
   (`docs/slices/INDEX.md`) that both sides appended to is the usual conflict: keep
   both lines.

4. Create a PR:
   - If $ARGUMENTS provided, use as title
   - Otherwise, derive title from the commit message
   - Body: summary of changes + test plan
   ```bash
   gh pr create --title "<title>" --body "..."
   ```
5. **Squash-merge once GitHub has settled the PR for the commit you pushed.**
   GitHub recomputes mergeability in the background after every push. A merge in
   that window was answered "merge conflicts" for a branch without any, the session
   merged main again and re-ran the gate, and push-to-merge reached a p90 of
   26.6 min. Every command below is one plain command with the values written out.

   a. **Read the PR:** `gh pr view --json number,headRefOid,headRefName`. From here
      on `<PR>`, `<SHA>` (the head you pushed) and `<BRANCH>` are written literally.

   b. **Wait for the settled state, in the background.** With
      `run_in_background: true`:
      `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py <PR> --head <SHA>`
      and act on its completion notification (do not poll it, do not `sleep`). It
      counts a state only once `headRefOid` is `<SHA>`, and stops at 180 s (six of
      seven measured settles took at most 26 s). Its last line and exit:
      - `0` `CLEAN` / `HAS_HOOKS` → merge now (c), in the same minute.
      - `6` `UNSTABLE` at the bound → mergeable, but a check that is not required is
        failing or pending: merge (c), and name those checks from
        `gh pr checks <PR>` in your report.
      - `3` `DIRTY` / `BEHIND` → `git fetch origin main`, then
        `git rev-list --count HEAD..origin/main`. `0` means the state was stale: run
        the wait again. Otherwise main moved: back to "Main moved since the gate?"
        above, then push and wait again.
      - `4` `BLOCKED` → a required check or review is missing: `gh pr checks <PR>`,
        report it, STOP. `--admin` only when the user explicitly asks to force:
        `gh pr merge <PR> --squash --admin --match-head-commit <SHA>`. Never silently.
      - `5` `merge_state_wait_deadline` → report the state it names and STOP; the
        user decides.
      - `7` → the PR is no longer open: go to (d).

   c. **Merge:** `gh pr merge <PR> --squash --match-head-commit <SHA>`. No pipe (the
      release gate refuses a piped release: a pipe reports its last command's
      status). No `--delete-branch`: in a worktree gh merges on GitHub, then checks
      out `main` to delete the local branch, fails with `fatal: 'main' is already
      used by worktree at …`, exits 1 after a SUCCESSFUL merge (2026-10-07 and
      10-12), and never reaches the remote branch, which it deletes after the local
      one. `--match-head-commit` makes GitHub refuse if the head moved since (b).

   d. **The verdict comes from the PR, never from the merge command's exit code:**
      `gh pr view <PR> --json state,mergeCommit -q '.state + " " + (.mergeCommit.oid // "-")'`.
      `MERGED <sha>` is the verdict; anything else is a failed merge, and the merge
      command's output is the reason.

   e. **Delete the remote branch, after `MERGED` only:**
      `gh api -X DELETE repos/:owner/:repo/git/refs/heads/<BRANCH>`, the PR's own
      head branch, never `main`/`master` (the release gate refuses deleting main
      through the API). A 422 "Reference does not exist" means the repository's
      "Automatically delete head branches" setting already removed it: done.

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

3. **Do not remove the worktree, and do not tell the user to remove it from another
   terminal.** Leaving is one step for the user: exit this session (Ctrl-C) and choose
   "Remove worktree". The plugin's SessionEnd hook (`scripts/worktree-session-end.sh`)
   then retires everything the worktree left outside its checkout — stack, volumes,
   images, dev server, Chrome and its profile — via the repo's
   `scripts/worktree-retire.sh`. A worktree that is kept instead is removed by the
   session-start sweep once its work is on main, it is clean and it has been unused
   for a week. Say this in one line when the merge is done.

**Merging does NOT deploy to production.** Deploy is decoupled: it fires only on a
`deploy-*` tag (or manual dispatch), not on a push to `main`. `/ship` lands your work
on `main`; nothing reaches a box until you explicitly run **`/deploy`**, and where a
project has a staging step (Endoxia, slice 603) that tag reaches STAGING only —
production is a separate **`/promote`**. This is intentional — merge freely, release
deliberately.
