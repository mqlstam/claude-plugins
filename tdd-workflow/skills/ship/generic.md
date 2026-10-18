# Ship: the flow for a repo without a land queue

For a repository whose `package.json` has no `ship:gate` and `ship:land`. Steps 1
and 2 of `SKILL.md` have run; "After it lands" there follows the merge below.

## How to run the gate (read this before step 3)

Measured over 47 ships: median invoke→merge **36 min**, p90 **140 min**, and **46%
of tool wall-clock was `sleep`/poll loops** around a gate that exceeds the 10-minute
`Bash` ceiling and gets backgrounded. Three rules, all non-negotiable:

1. **Split the gate into calls that FIT under the ceiling.** Never one
   `lint && typecheck && test && build && every check` mega-command: one call per
   command, so a failure names itself and a re-run repeats only what failed.
2. **Anything that might exceed ~8 minutes runs with `run_in_background: true`, and
   you WAIT FOR THE COMPLETION NOTIFICATION.** Do not write a `sleep 60`/poll loop.
   Do not re-run a command "to see where it is". While a background call runs, do
   something useful in the foreground (read the diff, draft the PR body) or simply
   wait. The plugin's `bash-shape-guard` hook refuses a foreground `sleep` above
   10 s, a `sleep` inside a loop, and a `| tail` whose status is then used, in
   subagents too.
3. **Independent steps run side by side.** Lint, typecheck and the format check read
   only the source and none waits on another: issue them in ONE message. Test and
   build start once those are green, in the background; step 5 runs after the
   build, because a check may read the build's output.

## Validation

3. **Find the repo's own commands.** `jq -r '.scripts | keys[]' package.json` lists
   them. The gate is the ones the repo has of `lint`, `typecheck`, `format:check`,
   `test` and `build`, each its own plain command through the repo's package
   manager: `pnpm <script>`, or `npm run <script>` where the repo has a
   `package-lock.json`. Print the ones it lacks: a command the repo does not have is
   skipped out loud, never silently.

4. **Run them.**
   - `pnpm lint`, `pnpm typecheck` and `pnpm format:check`: foreground, in one
     message. The format check runs BEFORE the commit, so a commit hook that formats
     has nothing to rewrite and the commit is the tree you checked.
   - `pnpm test`, then `pnpm build`: each with `run_in_background: true`, then wait
     for the notification.

   STOP on failure. A task runner that replays a cached pass (Turborepo's
   `FULL TURBO`) re-proves nothing after a failure: run it again with its cache off
   (`--force`).

5. **Project invariants.** Every other script in that list whose name starts with
   `check:` or `env:`, except the `:update` variants, each as its own plain command
   (`pnpm check:markers`, `pnpm env:drift`, ...). An updater OVERWRITES the
   committed baseline with whatever the tree measures, so it cannot fail; running
   one here reports green while redefining the target.

   If an assertion legitimately needs a new baseline, update it DELIBERATELY as part
   of the change, ideally by hand-editing only the entries your diff moved, and say
   so in the PR. Never as a side effect of the pre-ship sweep.

These local checks are the primary safety net when remote CI workflows are disabled.
Treat any failure as blocking.

## Ship

Steps 1-3 in a SINGLE message; then the PR; then the merge.

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
3. **Push**, as its own plain command (git inside a compound command is refused in a
   worktree session): `git push -u origin <branch>`.

   **Main moved since the gate?** `git fetch origin main`, then
   `git rev-list --count HEAD..origin/main`, before pushing. If it is not 0, merge
   `origin/main` in (`git merge --no-edit origin/main`), resolve, commit, and run
   Validation (steps 3-5) again on the merged tree before you push: without a land
   queue, nothing else tests the result of the merge.

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
