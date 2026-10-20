---
name: merge
description: Squash-merge a PR directly into main. Runs when the user types /merge, or after /approve merge (one run).
release-actions: merge
argument-hint: "[PR number or branch name]"
---

# Merge

Every command is one plain call with literal values. Never pipe the merge.
Use this merge approval throughout; do not invoke another release skill.
Never rewrite history or force-push.
Read main's newest `endoxia/main-health` status with
`gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
Print a red and whether this PR fixes it. A red main never blocks a ship or this merge; main CI checks the merged
range and promote gates production. Report missing or pending health too.

## Process

1. **Identify the PR.** `$ARGUMENTS` is the PR number or branch, else the current
   branch: `gh pr view <PR-or-branch> --json number,state,headRefOid,headRefName`.
   `<PR>`, `<SHA>` (its head) and `<BRANCH>` are written literally from here on.
   State must be `OPEN`; otherwise STOP and report it.

   **VERIFY covers the PR head, always.** `git fetch origin <BRANCH>`, then
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit <SHA>` must exit 0. It runs from any
   checkout of this repository: it finds the verdict of the worktree that reviewed
   the work, including integrations it accepted. A missing verdict means VERIFY
   first; changed contents get a review of the paths it names.

   **Prepare the existing PR when needed, under this merge approval.** Inspect
   its validation evidence against `<SHA>`. If integration or validation is needed,
   use the PR branch's own checkout, with a clean tree and HEAD equal to `<SHA>`.
   If this session is elsewhere, STOP and name the required checkout; never switch
   a shared branch. Then `git fetch origin main`
   and `git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
   Resolve conflicts and finish the merge with that same conventional subject;
   keep commit hooks enabled.
   Right after the merge commit, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py integrate`. Exit 0: the
   merge is exactly git's clean merge of the reviewed work and main, and the verdict
   covers it without another review. Exit 1 names the paths a conflict resolution or
   a hook changed: give them and the contracts they touch a targeted correctness
   review, and once it is READY run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py record`.
   Substantive feature changes re-arm VERIFY fully.
   Read this checkout's `package.json` after merging: run `pnpm ship:check` when
   declared, otherwise follow `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`.
   Only exit `0` passes; fix a failure or environment refusal before continuing.
   Use background execution and its completion notification for long commands.
   Before every push, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must exit 0, so
   nothing a hook or a later commit added goes out unreviewed. Push updates with
   `git push origin <BRANCH>`, and set `<SHA>` to the commit you checked and pushed
   (`git rev-parse HEAD`). Read the existing PR again. When the PR's `headRefOid` differs from it, someone else pushed: fetch
   that commit and run `verify-state.py check --commit` on it; adopt it only when
   that exits 0, otherwise STOP and report the unreviewed head. A non-fast-forward rejection requires
   inspecting the new remote head and validating the integrated tree; never force
   the push. Keep this PR; do not create another one.

2. **Wait for GitHub to settle its mergeability**, in the background
   (`run_in_background: true`), and act on the completion notification:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py <PR> --head <SHA>`.
   Right after a push GitHub still reports the previous head's state, and a merge
   in that window is answered "merge conflicts" for a branch without any.
   - `0` `CLEAN` / `HAS_HOOKS` → merge (3).
   - `6` `UNSTABLE` at the 180 s bound → a check that is not required is failing or
     pending. `gh pr checks <PR>`: a check from a workflow that is disabled in the
     Actions UI (`gh api 'repos/{owner}/{repo}/actions/workflows' --jq '.workflows[] | "\(.state)  \(.name)"'`)
     reflects a stale run and is ignored; otherwise name it in the report. Merge (3).
   - `3` `DIRTY` / `BEHIND` → conflicts or main moved: STOP and report.
   - `4` `BLOCKED` → a required check or review is missing: STOP and report.
     `--admin` only when the user explicitly asks to force:
     `gh pr merge <PR> --squash --admin --match-head-commit <SHA>`. Never silently.
   - `5` `merge_state_wait_deadline` → report the state it names and STOP.
   - `7` → no longer open: go to (4).

3. **Squash-merge:** `gh pr merge <PR> --squash --match-head-commit <SHA>`.
   No `--delete-branch`: in a worktree gh merges on GitHub, then checks out `main`
   to delete the local branch, fails with `fatal: 'main' is already used by
   worktree at …` and exits 1 after a SUCCESSFUL merge, before it reaches the
   remote branch.

   **Merging does NOT deploy.** Deploy fires only on a `deploy-*` tag (or manual
   dispatch), never on a push to `main`. This brings work onto `main`; run
   **`/deploy`** when you want it released. Where a project has a staging step
   (Endoxia, slice 603) that tag reaches STAGING, and production is a separate
   **`/promote`**.

4. **The verdict comes from the PR, never from the merge's exit code:**
   `gh pr view <PR> --json state,mergedAt,mergeCommit --jq '{state, mergedAt, sha: .mergeCommit.oid}'`.
   `MERGED` is the verdict; anything else is a failed merge, and the merge
   command's output is the reason.

5. **Delete the remote branch, after `MERGED` only:**
   `gh api -X DELETE repos/:owner/:repo/git/refs/heads/<BRANCH>`. Never `main` or
   `master`. A 422 "Reference does not exist" means the repository already deleted
   it ("Automatically delete head branches").

## Local cleanup

**Do NOT print cleanup instructions.** When the session is running inside
a worktree, Claude Code's exit prompt offers to remove the worktree
automatically. The remote branch was deleted in step 5. Local branch /
worktree pruning is the harness's responsibility, not the skill's.

Git itself refuses `git checkout main` / `git branch -d` from a worktree
whose `main` is checked out in a sibling tree (`fatal: 'main' is already
used by worktree at …`; `gh pr merge --delete-branch` prints the same after
a successful remote merge), so attempting it would just produce noise.

## Output

```
Merge Complete!
===============

PR #42: feat: add user notifications
Squash-merged into main as <sha> (admin override: <reason if any>).

HEAD on main: <sha> <subject>
```

End the response there. No "to clean up, run …" footer.
