---
name: merge
description: Squash-merge a PR into main and clean up the branch
disable-model-invocation: true
argument-hint: "[PR number or branch name]"
---

# Merge

Squash-merge an existing PR into main, then clean up.

## Process

1. **Identify PR**
   If $ARGUMENTS provided, use as PR number or branch name.
   Otherwise detect from current branch:
   ```bash
   gh pr view --json number,title,state,mergeable,mergeStateStatus
   ```

2. **Verify mergeable**
   - State must be `OPEN`
   - `mergeable` must be `MERGEABLE` (no conflicts)
   - If not mergeable, STOP and report why.

3. **Inspect checks**
   ```bash
   gh pr checks
   ```
   Three buckets to consider:
   - **Failing required checks** that ARE active workflows → STOP, report.
   - **Failing checks from a disabled workflow** (e.g. `CI`, `Smoke`
     when those workflows have been disabled in the Actions UI) → these
     reflect a stale run; ignore them and proceed via `--admin`.
   - **Pending required checks** on an active workflow → wait or, if the
     user explicitly said to force-merge, proceed via `--admin`.

   Verify which workflows are disabled before deciding:
   ```bash
   gh api 'repos/{owner}/{repo}/actions/workflows' \
     --jq '.workflows[] | "\(.state)  \(.name)"'
   ```

4. **Squash merge** (try in order, stopping at first success)
   ```bash
   gh pr merge <number> --squash --delete-branch \
     || gh pr merge <number> --squash --admin --delete-branch
   ```
   `--admin` bypasses required-check failures and branch-protection
   rules. Use it when:
   - Branch protection has no required checks configured (auto-merge errors).
   - Required checks are stale results from a now-disabled workflow.
   - The user explicitly asked to force-merge.

5. **Clean up local branches** (skip if the worktree owns `main`)
   ```bash
   git fetch --prune
   # If you're not currently checked out on the merged branch, just prune:
   git branch -d <feature-branch> 2>/dev/null || true
   ```
   Do NOT `git checkout main` if main is already checked out by a sibling
   worktree — the destructive-git hook will block it. Run `gh pr merge
   --delete-branch` instead and let the remote prune handle the
   server-side branch; the local branch can be cleaned up later from the
   tree that actually owns `main`.

6. **Verify**
   ```bash
   gh pr view <number> --json state,mergedAt,mergeCommit \
     --jq '{state, mergedAt, sha: .mergeCommit.oid}'
   ```

## Output

```
Merge Complete!
===============

PR #42: feat: add user notifications
Squash-merged into main (admin override applied because CI workflow disabled).
Local branch cleanup deferred to main worktree.

HEAD: abc123 - feat: add user notifications (#42)
```
