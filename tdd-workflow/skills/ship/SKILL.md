---
name: ship
description: Validate the change, commit, merge main, push, open a PR and squash-merge directly. Runs when the user types /ship, or after /approve ship (one run).
release-actions: merge
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`

Each command is one plain call, with literal values. Never pipe a release command.
Stay on this branch; use a feature branch for /ship. Main uses /quickship.

## Steps

1. **Something to ship.** If there is neither a change nor an unmerged commit, STOP.

2. **VERIFY before changing HEAD.** Read `.claude/.verify-state.json`: its `headSha`
   must equal `git rev-parse HEAD`. Missing or stale state means complete VERIFY
   through /feature or /refactor first. Check it now, before committing and before
   merging main; those steps do not re-arm VERIFY. A new feature change does.

3. **Commit.** Stage relevant files and create a conventional commit. Read the diff
   first and keep unrelated changes out. The working tree must then be clean.

4. **Merge main in.** `git fetch origin main`, then
   `git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
   Resolve conflicts in this branch, preserving both changes, and finish the merge
   commit with that same conventional subject.
   Keep commit hooks enabled. Do not rewrite history or force-push.
   An ordinary integration conflict does not restart VERIFY;
   substantive new feature work does. Local validation below checks the merged tree.

5. **Local validation.** Read the checkout's own `package.json` after the merge.
   When it declares `ship:check`, run `pnpm ship:check` once. It selects affected
   lint/typecheck, scoped assertions and directly touched tests. The model may add
   `--add-tests`, `--add-package` or `--add-checks`, each with `--reason`, for a
   boundary the automatic selection cannot see; nothing narrows the floor.
   Exit `0` passes, `1` fails, `2` refuses (fix the named environment issue, then
   retry). Any other nonzero exit or interrupted command is not a pass: report its
   output and resolve the cause. No automatic full-suite escalation.
   Without that script, follow `${CLAUDE_SKILL_DIR}/generic.md` (the repo's own
   lint, typecheck, formatting, tests, build and assertions).
   Independent checks may run together. A call that can exceed about 8 minutes
   runs with `run_in_background: true`; wait for its completion notification,
   never sleep-poll. Any validation failure blocks this ship.

6. **Push and open the PR.** `git push -u origin <branch>`, then
   `gh pr create --title "<title>" --body-file <body-file>`. Write the summary and
   validation evidence to that file first. Use $ARGUMENTS as title when supplied.
   Read main's newest `endoxia/main-health` status with
   `gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
   A red main never blocks a ship: print the failing status and say whether this
   change fixes it. Missing or pending health is reported too; an API error means
   unknown health and does not block this ship. Main's short CI
   checks the merged range after the merge; the whole suite gates production.

7. **Squash-merge once GitHub has settled the PR for the commit you pushed.**
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
        the wait again. Otherwise main moved: use the merge command from step 4
        with its conventional subject, resolve conflicts, run local validation
        again, then push and wait again. Do not re-arm VERIFY solely for that merge.
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


## After it lands

1. **Stop what this worktree started, in this order: dev server, Chrome, containers.**
   Stop BEFORE deleting build output: `rm -rf packages/web/.next` under a live
   `next dev` corrupts the server's state. Each entry point is pidfile-scoped and
   idempotent, so calling it when nothing runs is a no-op.

   ```bash
   bash scripts/wt.sh ./scripts/worktree-dev-detached.sh --stop 2>/dev/null || true
   bash scripts/wt.sh ./scripts/worktree-chrome.sh --stop 2>/dev/null || true
   bash scripts/wt.sh docker compose stop 2>/dev/null || true
   ```

2. **Delete only `packages/web/.next`** (a stale one has skewed a bundle budget and
   filled the disk). Never `packages/*/dist`: `pnpm env:drift` and the checks read
   `packages/shared/dist`, and a worktree stripped of it cannot run its next gate.

   ```bash
   rm -rf packages/web/.next
   ```

3. **Do not remove the worktree, and do not tell the user to remove it from another
   terminal.** Leaving is one step for the user: exit this session (Ctrl-C) and
   choose "Remove worktree"; the plugin's SessionEnd hook then retires what the
   worktree left outside its checkout (stack, volumes, images, dev server, Chrome)
   via the repo's `scripts/worktree-retire.sh`. A kept worktree is removed by the
   session-start sweep once its work is on main, it is clean and unused for a week.
   Say this in one line.

   **In a T3 Code thread** (the session runs in `~/.t3/worktrees/...`, or
   `CLAUDE_CODE_ENTRYPOINT` is `sdk-*`) there is no exit prompt: settle the thread
   in T3 (T3 also settles it once the PR merged). Where the repo reads T3's state
   (Endoxia, slice 752) the session-start sweep then stops its stack and removes
   its checkout when it is clean and its work is on main. Say that instead.

**Landing does NOT deploy.** A deploy fires only on a `deploy-*` tag (or a manual
dispatch), never on a push to `main`. `/ship` puts the work on `main`; `/deploy`
releases it, to STAGING where the project has one (Endoxia, slice 603), and
production is a separate `/promote`.
