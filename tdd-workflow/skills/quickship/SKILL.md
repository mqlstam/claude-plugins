---
name: quickship
description: Validate and push directly to main without a PR; where the repo lands through a queue, it is /ship. Runs when the user types /quickship, or after /approve quickship (one run).
release-actions: push-main, merge
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm *), Bash(pnpm *), Bash(node *), Bash(jq *)
---

# Quick Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`
- Worktree: !`git rev-parse --show-toplevel`
- Primary worktree: !`git worktree list --porcelain | head -2 | tail -1 | awk '{print $2}'`
- Land queue: !`jq -r 'if (.scripts // {} | has("ship:gate") and has("ship:land")) then "yes" else "no" end' package.json 2>/dev/null || echo no`

## Land queue `yes`: /quickship is /ship

Where the repo lands through a queue (`package.json` has `ship:gate` and
`ship:land`, Endoxia since slice 803), the queue is the only way onto `main`:
nothing is pushed to `main` directly, and a PR is what the queue lands. So:

- On `main` (no branch to land) → STOP: "Here the land queue is the only way onto
  main: run /ship from a worktree branch."
- Otherwise read `${CLAUDE_PLUGIN_ROOT}/skills/ship/SKILL.md` and follow it from
  its Context (run those commands yourself), with `$ARGUMENTS` as the commit
  subject. This /quickship's approval covers its land.

Stop reading here. Everything below is for a repo without a land queue.

## Land queue `no`: straight to main

Pushes straight to main. Skips the PR + auto-merge dance that `/ship` runs.
Use only when CI is disabled, branch protection is unconfigured, and the
audit trail of a PR is not required. For most agentic-development setups,
prefer `/ship` — pushing agent-generated code direct to main without a PR
is the documented highest-risk pattern.

## Validation

Based on the context above:

1. **Must be on main, in the primary worktree.** This skill commits directly to
   main; a worktree session never switches branches, and git refuses to check out
   `main` where another worktree holds it. If `Branch` ≠ `main` or `Worktree` ≠ `Primary worktree`,
   STOP with: "Use /ship from a feature branch or worktree; /quickship only runs
   from the main checkout."

2. **No changes** — if there are no changes to commit, STOP.

3. **VERIFY-state gate** — same as /ship. The parent skill's VERIFY phase must
   have run on the current diff:

   ```bash
   if [ ! -f .claude/.verify-state.json ]; then
     echo "VERIFY phase has not run. Complete it (re-enter /feature or /refactor) before /quickship."
     exit 1
   fi
   verified_sha=$(jq -r .headSha .claude/.verify-state.json 2>/dev/null || cat .claude/.verify-state.json)
   if [ "$verified_sha" != "$(git rev-parse HEAD)" ]; then
     echo "VERIFY-state is stale ($verified_sha != HEAD). Re-run VERIFY before /quickship."
     exit 1
   fi
   ```

4. **The gate: the same as `/ship`'s.** Read
   `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md` and follow its "How to run the
   gate" and its "Validation" (steps 3-5), then come back here: the repo's own lint,
   typecheck, format check, test and build, then its `check:*` / `env:*` scripts,
   each its own call, the long ones in the background. STOP on any failure.

## Ship (do everything below in a SINGLE message)

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
   - If $ARGUMENTS provided, use as commit message
3. Push to main: `git push origin main`

Do all of the above in a single response. Do not send any other text besides
the tool calls.

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
