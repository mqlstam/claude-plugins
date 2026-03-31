# Phase 2: TEST (Parallel by layer)

## Goal
Write tests for the NEW structure before implementing it. Tests must FAIL (RED).

## Agent
Use `refactor-test-writer` — one per affected layer, launched in parallel if multiple layers.

## Steps
1. For each affected layer, launch a test writer in parallel:
   ```
   refactor-test-writer → "Write tests for {layer} layer - target: {refactoring target}"
   ```

   Example with 3 layers:
   ```
   refactor-test-writer → "Write tests for service layer - new AuthService API"
   refactor-test-writer → "Write tests for route layer - new auth endpoints"
   refactor-test-writer → "Write tests for hook layer - new useAuth hook"
   ```

2. Each test writer:
   - Writes tests for the NEW structure (not the old one)
   - Runs tests — must FAIL (RED state)
   - Reports RED confirmation

3. Wait for ALL test writers to report RED

## Completion
- Tests written for all affected layers
- All tests confirmed FAILING (RED)
- Mark test task as completed, move to REFACTOR phase
