# Phase 3: WIRE

## Goal
Connect the built layers end-to-end: hooks to components, routes to navigation, event handlers wired.

## Agent
Delegate to the `wiring-agent`.

## Steps
1. Connect hooks to components (pass data, handle loading/error states)
2. Add routes to navigation/routing config
3. Wire event handlers (form submissions, button clicks → hook mutations)
4. Verify the full path works: UI → hook → API → service → database

## Completion
- All layers connected
- No orphan imports or dead code
- Mark wire task as completed, move to REVIEW phase
