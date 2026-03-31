# Phase 6: VERIFY

## Goal
Final end-to-end validation. Confirm the refactoring is complete and nothing is broken.

## Agent
Delegate to the `integration-reviewer` agent.

## Steps
1. Run ALL tests together (confirms review fixes didn't break anything)
2. Check for dead code (old implementations that should have been deleted)
3. Verify test coverage is maintained or improved
4. Verify no regressions in related functionality

## Completion
- All tests pass
- No dead code remains
- Coverage maintained
- Mark verify task as completed
- Refactoring is ready to ship — suggest `/ship` to the user
