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
   gh pr view --json number,title,state,mergeable
   ```

2. **Verify mergeable**
   - State must be OPEN
   - No merge conflicts
   - If not mergeable, STOP and report why.

3. **Check CI**
   ```bash
   gh pr checks
   ```
   If checks failing or pending, STOP and report.

4. **Squash merge**
   ```bash
   gh pr merge <number> --squash
   ```

5. **Clean up**
   ```bash
   git checkout main
   git pull origin main
   git branch -d <feature-branch>
   ```

6. **Verify**
   ```bash
   git log --oneline -1
   ```

## Output

```
Merge Complete!
===============

PR #42: feat: add user notifications
Squash-merged into main.
Local branch cleaned up.

HEAD: abc123 - feat: add user notifications (#42)
```
