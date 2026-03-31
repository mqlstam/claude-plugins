# Phase 5: INTEGRATION

## Goal
Final end-to-end validation. Confirm everything works together after review fixes.

## Agent
Delegate to the `integration-reviewer` agent.

## Steps
1. Run all tests together (confirms review fixes didn't break anything)
2. Verify layer connections (routes → services → hooks → components)
3. Check browser console for errors (if UI changes)
4. Check network requests (if API changes)

## Completion
- All tests pass
- No console errors
- No broken connections
- Mark integration task as completed
- Feature is ready to ship — suggest `/ship` to the user
