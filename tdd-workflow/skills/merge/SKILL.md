---
name: merge
description: Squash-merge a PR into main, through the land queue where the repo has one. Runs when the user types /merge, or after /approve merge (one run).
release-actions: merge
land-queue: only
argument-hint: "[PR number or branch name]"
---

# Merge

Squash-merge an existing PR into main. Do not print manual cleanup
instructions here: `/ship` owns post-merge cleanup (it stops this worktree's
processes and deletes its build output), and Claude Code's exit prompt removes
the worktree.

## Context (pre-computed)

- Land queue: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/land_queue.py --fetch`

Every command below is one plain command with the values written out literally:
no variables, no `$(...)`, no pipe on the merge (the release gate refuses a piped
release, and a worktree session refuses commands it cannot read).

How `main` lands decides the flow, not this checkout: the `Land queue` line fetched
`origin main` and read `origin/main`'s `package.json`.

- `queue` → "Land queue `queue`" below.
- `merge-main-first` → STOP: "main lands through the queue now; this checkout
  predates it: `git merge origin/main` first, then /merge."
- `unknown: …`, or anything else (no verdict, an error) → STOP and report the line:
  without knowing how main lands, nothing merges.
- `none` → "Land queue `none`" below.

## Land queue `queue`: the queue lands it

Where `main` has `ship:land` (Endoxia since slice 803) the land queue is the only
way onto `main`: nothing merges a PR around it, and the release gate refuses a
bare PR merge or a merge through the API under this skill there. The queue's
integrator gates the merged result once and squash-merges in order.

1. **Identify the PR.** `$ARGUMENTS` is the PR number or branch, else the current
   branch: `gh pr view <PR-or-branch> --json number,state,headRefName`. `<PR>` is
   its number and `<branch>` its head branch; state must be `OPEN`, otherwise STOP
   and report it.
2. **Land it**, with `run_in_background: true` and the Bash `timeout` at `7200000`:
   `pnpm ship:land land --pr <PR> --max-wait-ms 7000000`
   then end your turn and act on the notification (`--max-wait-ms` keeps the
   queue's own deadline inside the background ceiling, less 200 s to read the
   exit). For a PR already queued (a `/ship` that hit its deadline) this
   re-attaches to that entry and waits for it again.
3. **Report by exit code.**
   - `0` landed: "Merge complete: PR #<PR> landed on main as <sha8>."
   - `3` textual conflict: name the files and the change it collided with; the
     branch needs `origin/main` merged in and `/ship` from its own worktree.
   - `1` the gate failed on the merged result and named this PR: say what failed;
     the fix belongs in the branch's worktree, then `/ship` there.
   - `4` refused: superseded by a newer push, or a batch was landing the old head
     → run step 2 again (after that batch); PR closed, retargeted, or GitHub
     refused → report the reason.
   - `6` main is red on its own; nothing of the batch merged. Main's red clears only
     when a batch lands whose gate passes what main fails, so waiting does not clear
     it. Read the evidence (what fails on `origin/main` alone, with its logs). When
     it names a timing-sensitive or flaky failure (a timeout, a test that passes
     when run again), run step 2 again: exit 6 did not use this approval. Otherwise
     report the evidence and STOP: the fix goes through the queue as its own PR, and
     landing it is what clears main; this PR lands after it. Never run
     `pnpm ship:land clear-main-red` yourself: that is the owner's call.
   - `5` merged, but main is not, or may not be, the tested tree (changed outside
     the queue, or the merges stopped part-way): STOP and tell the owner, quoting it.
   - `2` the land could not decide (environment, a merge not yet reported, two
     reds of one tree that share no step, a main red the queue's own passing runs
     contradict, a bisect whose reds disagreed, an internal error): run step 2 once
     more; a second `2`, report it.
     It promises nothing, so never report the PR as not merged.
   - `75` the queue ahead outlasted the deadline: #<PR> is still queued
     (`pnpm ship:land status`); `/merge <PR>` waits again.

   After `0`, delete the remote branch: `git push origin --delete <branch>` (a
   "remote ref does not exist" means the repository already did).

   A land that exited `1`, `3`, `4` or `6` landed nothing, and pnpm's `254`
   (command not found) never started one: neither used this approval; every other
   exit used it.

**Landing does NOT deploy.** Run `/deploy` when you want it released.

Stop reading here. Everything below is for a repo without a land queue.

## Land queue `none`: process

1. **Identify the PR.** `$ARGUMENTS` is the PR number or branch, else the current
   branch: `gh pr view <PR-or-branch> --json number,state,headRefOid,headRefName`.
   `<PR>`, `<SHA>` (its head) and `<BRANCH>` are written literally from here on.
   State must be `OPEN`; otherwise STOP and report it.

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
