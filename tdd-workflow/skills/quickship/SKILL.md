---
name: quickship
description: Validate, commit, merge origin/main and push directly to main without a PR. Runs when the user types /quickship, or after /approve quickship (one run).
release-actions: push-main
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm *), Bash(pnpm *), Bash(node *), Bash(jq *)
---

# Quick Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Worktree: !`git rev-parse --show-toplevel`
- Worktrees: !`git worktree list --porcelain`

1. **Only main, in the primary checkout.** Otherwise STOP and use /ship from the
   feature branch. Never switch branches in a shared tree. If nothing changed, STOP.
2. **VERIFY before changing HEAD.** `.claude/.verify-state.json` must exist and its
   `headSha` must equal `git rev-parse HEAD`. Otherwise complete VERIFY first.
   Check before committing and merging main, so integration does not re-arm VERIFY.
3. **Commit.** Stage relevant changes and create a conventional commit, using
   $ARGUMENTS when supplied. Keep unrelated changes out.
4. **Integrate.** `git fetch origin main`, then
   `git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
   Resolve conflicts and finish the merge with that same conventional subject.
   Keep commit hooks enabled. Never rewrite history or force-push.
5. **Validate the merged tree.** Read this checkout's own `package.json` after
   merging. With `ship:check`, run `pnpm ship:check`; add tests, packages or checks
   only with a reason, never narrow its floor. Exit `0` passes; `1` fails; `2`
   refuses the environment. Resolve the named cause; no other exit is a pass.
   Without the script follow `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`.
   Background calls that can exceed about 8 minutes and wait for notification.
6. **Report main health.** Read the newest `endoxia/main-health` status on origin's
   main tip; print red, pending or missing status and whether this change fixes a
   red. A red main does not block this push.
7. **Push.** `git push origin main`. A non-fast-forward rejection means main moved:
   fetch, merge and validate again, then retry. Do not restart VERIFY just for that
   integration. Confirm the pushed commit; this does not deploy.

## After the push

```bash
rm -rf packages/web/.next
```

`/quickship` runs from the primary checkout, so there is no worktree to reclaim.

**Delete only `.next` — never `packages/*/dist`.** Same rule as `/ship`, and the
primary checkout is not an exception to it: a repo's checks may read a package's
build output, so a checkout stripped of `dist` cannot run its own next gate, and the
failure reads as a red gate rather than as a missing build — which is worse than
the disk it frees.
