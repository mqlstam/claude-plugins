# Phase 2: BUILD (Parallel)

## Goal
Implement each layer using TDD. Launch builders in parallel for independent layers.

## Agents
Launch IN PARALLEL (single message) for each in-scope layer:

| Layer | Agent | TDD cycle |
|-------|-------|-----------|
| Schema | `schema-builder` | Write migration test → create schema/migration |
| Service | `service-builder` | Write service test → implement business logic |
| Route | `route-builder` | Write endpoint test → implement route handler |
| Hook | `hook-builder` | Write hook test → implement TanStack Query hook |
| Component | `component-builder` | Write component test → implement React component |

Each builder follows RED → GREEN:
1. Write a failing test (RED)
2. Implement minimum code to pass (GREEN)
3. Report completion

## Steps
1. Provide each builder with: feature name, spec output (types + contract), its task card
2. Launch all in-scope builders in a single message
3. Wait for all to complete
4. Verify all tests pass together: `npm run test`

## Completion
- All builders report GREEN
- All tests pass when run together
- Mark build task as completed, move to WIRE phase
