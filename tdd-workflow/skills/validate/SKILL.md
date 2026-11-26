---
name: validate
description: Validate a vertical slice - each failure that can really happen and hurts is caught, the layers are connected, the touched tests pass
disable-model-invocation: true
allowed-tools: Bash(npm run test*), Bash(pnpm test*), Bash(pnpm run test*), Bash(pnpm precheck*), Bash(pnpm ship:check*)
---

# Validate Slice

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Changed files: !`git diff --name-only HEAD`
- Status: !`git status --short`

## Checks

Based on the changed files above:

1. **Failures caught**: for the change as a whole, which failures can really happen and
   would hurt? For each, name what catches it: a run on the real surface, a test in the
   change, an existing test, the type system, staging, or the gate before production. A failure nothing catches
   is an issue. A changed file without a test file is not: never count test files.
   A test in the change that catches no such failure, or one already caught elsewhere
   or cheaper, is an issue too: it is deleted.
2. **Run tests**: where the project declares `precheck`, `pnpm precheck`; else where it declares `ship:check`, `pnpm ship:check`: either runs the changed files' tests and what they reach. Otherwise the test files of the changed files, with the project's per-file test command. All must pass. A whole-suite run needs a stated reason.
3. **Connections**: routes import services, hooks call endpoints, components use hooks.
4. **Orphans**: unused imports, dead code, disconnected files.

## Report

```
Vertical Slice Validation
=========================

Failures that can happen and hurt:
  [pass] another tenant's row returned by findById -> auth.service.test.ts
  [pass] a renamed response field -> the shared type
  [fail] a second submit creates a second session -> nothing catches it
  [fail] auth.routes.test.ts "calls the service" -> catches nothing the types do not; delete

Test Results:
  [pass] All touched tests passing

Connections:
  [pass] routes/auth.routes.ts -> services/auth.service.ts
  [fail] components/AuthForm.tsx -> hooks/useAuth.ts (imported but not called)

Issues Found: 3
Status: INCOMPLETE
```

- **PASS**: every such failure is caught by something named, no test fails the keep
  rule, the touched tests pass, all connections verified
- **FAIL**: List specific issues
