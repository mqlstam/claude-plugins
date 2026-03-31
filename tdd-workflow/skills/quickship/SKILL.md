---
name: quickship
description: Validate, commit, and push directly to main without a PR
disable-model-invocation: true
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(npm run test*), Bash(pnpm test*), Bash(pnpm run test*)
---

# Quick Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`

## Validation

Based on the context above:

1. If not on main, switch to main first.
2. If all tests are not already known to pass, run tests. If they fail, STOP.
3. If there are no changes to commit, STOP.

## Ship (do everything below in a SINGLE message)

1. Stage all relevant changed files
2. Create a commit using conventional format (`feat:`, `refactor:`, `fix:`)
   - If $ARGUMENTS provided, use as commit message
3. Push to main: `git push origin main`

Do all of the above in a single response. Do not send any other text besides the tool calls.
