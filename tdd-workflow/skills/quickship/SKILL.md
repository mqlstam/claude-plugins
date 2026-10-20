---
name: quickship
description: Validate, commit, merge origin/main and push directly to main without a PR. Runs when the user types /quickship, or after /approve quickship (one run).
release-actions: push-main
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py *)
---

# Quick Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Worktree: !`git rev-parse --show-toplevel`
- Worktrees: !`git worktree list --porcelain`

1. **Only main, in the primary checkout.** Otherwise STOP and use /ship from the
   feature branch. Never switch branches in a shared tree. If nothing changed, STOP.
2. **VERIFY before changing HEAD.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check` must exit 0: the READY
   verdict in `.claude/.verify-state.json` covers exactly the current contents.
   Otherwise complete VERIFY first, or review the changed paths it names.
   Check before committing and merging main, so integration does not re-arm VERIFY.
3. **Commit.** Stage relevant changes and create a conventional commit, using
   $ARGUMENTS when supplied. Keep unrelated changes out.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must
   then exit 0: the commit holds exactly the reviewed contents. An index staged
   before the review can commit other contents than the files on disk, which the
   working-tree check in step 2 cannot see. On a refusal, review the paths it names.
4. **Integrate.** `git fetch origin main`, then
   `git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
   Resolve conflicts and finish the merge with that same conventional subject.
   Right after the merge commit, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py integrate`. Exit 0: the
   merge is exactly git's clean merge of the reviewed work and main, and the verdict
   covers it without another review. Exit 1 names the paths a conflict resolution or
   a hook changed: give them and the contracts they touch a targeted correctness
   review, and once it is READY run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py record`.
   The same holds for every later integration retry.
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
7. **Push.** Before every push, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must exit 0, so
   nothing a hook or a later commit added goes out unreviewed. Then `git push origin main`. A
   non-fast-forward rejection means main moved: fetch, merge, `integrate` and
   validate again, then retry. Confirm the pushed commit; this does not deploy.

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
