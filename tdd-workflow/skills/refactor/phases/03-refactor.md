# Phase 3: REFACTOR (Sequential)

## Goal
Implement the new structure while the tests that hold the behaviour stay green. Sequential because layers have dependencies.

## Steps
For each affected layer in dependency order (schema → service → route → hook → component):

1. Implement the new structure
2. Run the test files that hold that layer's behaviour (existing ones and any written
   in TEST), with the project's per-file test command (Endoxia:
   `pnpm --filter <pkg> test <file>…`) — they must pass
3. Old code still exists (not migrated yet)

Work through layers sequentially. A layer's tests pass before moving to the next.

## Completion
- All new structure implemented
- The tests that hold the moved behaviour pass
- Old code still in place (callers not yet migrated)
- Mark refactor task as completed, move to MIGRATE phase
