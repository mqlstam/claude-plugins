---
name: ship
description: Validate, commit, push, and create PR for current work
disable-model-invocation: true
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`
- Recent commits on this branch: !`git log --oneline -5`

## Validation

Based on the context above:

1. If there are no changes to commit, STOP.
2. Run quality checks (skip any that don't exist in this project):
   - `pnpm lint` — if it fails, STOP.
   - `pnpm typecheck` — if it fails, STOP.
   - `pnpm test` — if it fails, STOP.

These local checks are the primary safety net when remote CI workflows
are disabled. Treat any failure as blocking — the goal is to ship green.

## Ship (do everything below in a SINGLE message)

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
3. Push the branch: `git push -u origin <branch>`
4. Create a PR:
   - If $ARGUMENTS provided, use as title
   - Otherwise, derive title from the commit message
   - Body: summary of changes + test plan
   ```bash
   gh pr create --title "<title>" --body "..."
   ```
5. Enable auto-merge if branch-protection allows it; fall back to admin
   merge when branch protection is unconfigured (no required checks =
   `gh pr merge --auto` errors with "Branch does not require checks").
   Try in this order, stopping at the first that succeeds:
   ```bash
   # Preferred: auto-merge waits for required checks, then squash-merges.
   gh pr merge --squash --auto --delete-branch \
     || gh pr merge --squash --admin --delete-branch \
     || echo "Manual merge required — see gh pr view"
   ```
   `--admin` bypasses required status checks (needs admin rights on the
   repo). Use it when (a) branch protection is unconfigured, or (b) the
   relevant CI workflows are disabled and you trust local validation.

Do all of the above in a single response. Do not send any other text besides the tool calls.
