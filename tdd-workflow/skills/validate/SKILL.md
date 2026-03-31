---
name: validate
description: Validate vertical slice completeness - test coverage, connections, test execution
disable-model-invocation: true
allowed-tools: Bash(npm run test*), Bash(pnpm test*), Bash(pnpm run test*)
---

# Validate Slice

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Changed files: !`git diff --name-only HEAD`
- Status: !`git status --short`

## Checks

Based on the changed files above:

1. **Test coverage**: for each changed `*.ts`/`*.tsx` (excluding test files), does a `{name}.test.{ext}` or `{name}.spec.{ext}` exist?
2. **Run tests**: `pnpm test` — all must pass.
3. **Connections**: routes import services, hooks call endpoints, components use hooks.
4. **Orphans**: unused imports, dead code, disconnected files.

## Report

```
Vertical Slice Validation
=========================

Files Changed:
  [pass] auth.service.ts (has test)
  [fail] auth.routes.ts (NO TEST)

Test Results:
  [pass] All 24 tests passing

Connections:
  [pass] routes/auth.routes.ts -> services/auth.service.ts
  [fail] components/AuthForm.tsx -> hooks/useAuth.ts (imported but not called)

Issues Found: 2
Status: INCOMPLETE
```

- **PASS**: All files have tests, all tests pass, all connections verified
- **FAIL**: List specific issues
