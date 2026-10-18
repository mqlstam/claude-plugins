# Ship: the flow for a repo without a land queue

For a repository whose `package.json` has no `ship:gate` and `ship:land`. Steps 1
and 2 of `SKILL.md` have run; "After it lands" there follows the merge below.

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

3. **Ask what already passed, and say what you are skipping.**

   Where the repo has `scripts/gate-record.mjs`, each command below records its
   green against a content key: the commit SHA **plus a tree hash covering
   uncommitted and untracked changes**. A command an earlier run proved on this
   exact content does not run again.

   ```bash
   node scripts/gate-record.mjs covered | sed 's/^/  reused from an earlier gate: /'
   ```

   Print that list. **Never skip silently**: a reader must be able to see which
   commands ran in this `/ship` and which were inherited. An empty list means
   everything below runs, which is the correct outcome, not a failure.

   If the repo has no `scripts/gate-record.mjs`, treat every command as uncovered
   and run the gate in full.

4. **The turbo lane: two bounded calls, under the machine-wide lock.**

   Where the repo has `scripts/gate-lock.mjs`, one heavy gate runs at a time across
   every worktree on this machine. `gate-lock run` waits visibly, names the holder,
   and releases on interrupt. Without it, run the commands bare.

   **Anchor the affected set to `origin/main`, and include `build`.** The remote
   deploy validates against `origin/main` over a full checkout and runs the
   production build. A local `--affected` computed against a stale/diverged local
   `main`, or one that omits `build`, tests a NARROWER set than what actually
   lands, so local-green ≠ deploy-green.

   Run these in a **plain shell, never through `scripts/wt.sh`**: the worktree
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

   Skip 4a and/or 4b if step 3 already listed them. STOP on failure, and do NOT
   accept a `FULL TURBO` (all-cached) re-run as proof after a failure; it re-proves
   nothing. Use `--force`.

   Non-Turborepo repos: `pnpm lint`, `pnpm typecheck`, `pnpm build`, and
   `pnpm test --changed origin/main` (else `pnpm test`), same split.

   ```bash
   # 4c — formatting, beside 4a (it takes no lock and reads only the source).
   #      lint-staged covers the commit path; this catches amends and out-of-scope
   #      adds. Run it BEFORE the commit so lint-staged has nothing to rewrite — a
   #      hook that reformats on the way in invalidates the gate record.
   pnpm format:check && node scripts/gate-record.mjs record format:check pass --by ship
   ```

5. **Project invariants: one parallel run, not ~66 serial cold starts.**

   ```bash
   pnpm gate:checks
   ```

   Where the repo defines it, it discovers every `check:*` / `env:*` script, runs
   them in parallel lanes, keeps each failure's full output, records each pass, and
   exits non-zero on any failure. It excludes the `:update` variants: an updater
   OVERWRITES the committed baseline with whatever the tree measures, so it cannot
   fail; running one here reports green while redefining the target.

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
   of the change, ideally by hand-editing only the entries your diff moved, and say
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

   `promote` re-keys the record onto the commit you just made, but only after
   recomputing the tree hash and finding it unchanged. If `git add` staged only part
   of the tree, or a `pre-commit` hook reformatted a file on the way in, it refuses,
   says why, and `pre-push` then runs in full. Skip `promote` where the repo has no
   `scripts/gate-record.mjs`.

   **Main moved since the gate?** Check `git rev-list --count HEAD..origin/main` before
   pushing. If it is not 0, merge `origin/main` in (`git merge --no-edit origin/main`),
   resolve, commit, then `promote` again. When main only carries what this branch
   already contains, the tree hash is unchanged and every record carries over;
   otherwise `promote` refuses and the gate re-runs on the merged tree, which is the
   point.

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

Then "After it lands" in `SKILL.md`.
