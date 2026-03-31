# Phase 3: REFACTOR (Sequential)

## Goal
Implement the new structure to make failing tests pass. Sequential because layers have dependencies.

## Steps
For each affected layer in dependency order (schema → service → route → hook → component):

1. Implement the new structure
2. Run layer tests — must PASS (GREEN)
3. Old code still exists (not migrated yet)

Work through layers sequentially. Each layer's tests must pass before moving to the next.

## Completion
- All new structure implemented
- All new tests passing (GREEN)
- Old code still in place (callers not yet migrated)
- Mark refactor task as completed, move to MIGRATE phase
