# Phase 1: ANALYZE (Parallel)

## Goal
Understand the refactoring target: what depends on it, what calls it, what tests cover it, and what layers are affected.

## Agents
Launch ALL four analyzers IN PARALLEL in a single message:

| Agent | Task |
|-------|------|
| `dependency-mapper` | Map what the target depends on (imports, services, DB) |
| `caller-finder` | Find all code that uses/calls the target |
| `test-scanner` | Find which behaviour of the target an existing test already holds |
| `impact-assessor` | Identify affected layers and modules |

## Steps
1. Launch all 4 agents with the refactoring target name
2. Wait for all to complete
3. Merge results into a refactoring plan:
   - Dependencies mapped
   - Callers identified
   - What already holds the behaviour being moved (tests, types), and the gaps
   - Affected layers and modules identified
   - Parallelization opportunities noted

## Completion
- Refactoring plan documented
- Affected layers and modules known
- Mark analyze task as completed, move to TEST phase
