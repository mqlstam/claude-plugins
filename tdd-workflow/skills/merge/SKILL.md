---
name: merge
description: Squash-merge a PR into main
disable-model-invocation: true
argument-hint: "[PR number or branch name]"
---

# Merge

Squash-merge an existing PR into main. Local-branch and worktree cleanup
is handled by Claude Code's exit prompt — do not print manual cleanup
instructions.

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
   Three buckets:
   - **Failing required checks on active workflows** → STOP, report.
   - **Failing checks from a disabled workflow** (e.g. `CI`, `Smoke`
     when those workflows have been disabled in the Actions UI) → these
     reflect a stale run; ignore them and proceed via `--admin`.
   - **Pending required checks** on an active workflow → wait, or
     proceed via `--admin` if the user explicitly asked to force-merge.

   Check which workflows are disabled before deciding:
   ```bash
   gh api 'repos/{owner}/{repo}/actions/workflows' \
     --jq '.workflows[] | "\(.state)  \(.name)"'
   ```

4. **Squash merge — inspect state, don't blind-guess `--admin`.**
   ```bash
   required=$(gh api repos/:owner/:repo/branches/main/protection/required_status_checks \
     --jq '.contexts | length' 2>/dev/null || echo 0)
   if [ "$required" = 0 ]; then
     gh pr merge <number> --squash --delete-branch     # no required check → clean
   else
     cs=$(gh pr checks <number> --json name,state -q '.[]|select(.name=="check")|.state' 2>/dev/null)
     [ "$cs" = SUCCESS ] && gh pr merge <number> --squash --delete-branch \
       || { echo "BLOCKED: 'check' is $cs — force only via FORCE_ADMIN=1"; \
            [ "${FORCE_ADMIN:-0}" = 1 ] && gh pr merge <number> --squash --admin --delete-branch || exit 2; }
   fi
   ```
   `--admin` only fires behind an explicit `FORCE_ADMIN=1` — never silently.

   **Merging does NOT deploy.** Deploy fires only on a `deploy-*` tag (or manual
   dispatch), never on a push to `main`. This brings work onto `main`; run
   **`/deploy`** when you want it on production.

5. **Verify**
   ```bash
   gh pr view <number> --json state,mergedAt,mergeCommit \
     --jq '{state, mergedAt, sha: .mergeCommit.oid}'
   ```

## Local cleanup

**Do NOT print cleanup instructions.** When the session is running inside
a worktree, Claude Code's exit prompt offers to remove the worktree
automatically. The remote branch is already gone via `--delete-branch`.
Local branch / worktree pruning is the harness's responsibility, not
the skill's.

The destructive-git hook also blocks `git checkout main` /
`git branch -d` from a worktree whose `main` is owned by a sibling tree,
so attempting it would just produce noise.

## Output

```
Merge Complete!
===============

PR #42: feat: add user notifications
Squash-merged into main as <sha> (admin override: <reason if any>).

HEAD on main: <sha> <subject>
```

End the response there. No "to clean up, run …" footer.
