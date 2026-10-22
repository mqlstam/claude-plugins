# Phase 4: MIGRATE (Parallel by module)

## Goal
Update all callers to use the new structure, then delete old code.

## Agent
Use `module-migrator` — one per affected module, launched in parallel if multiple modules.

## Steps
1. For each affected module, launch a migrator in parallel:
   ```
   module-migrator → "Migrate {module} to use new {refactoring target}"
   ```

   Example with 3 modules:
   ```
   module-migrator → "Migrate admin module to use new AuthService"
   module-migrator → "Migrate user module to use new AuthService"
   module-migrator → "Migrate dashboard module to use new AuthService"
   ```

2. Each migrator:
   - Updates imports in their module
   - Updates call sites
   - Runs the test files of the call sites it changed, per file
   - Reports completion

3. After ALL modules migrated:
   - Delete old code entirely
   - Remove old tests
   - Clean up unused imports
   - No backward compatibility — the old version is gone completely

## Completion
- All callers migrated
- Old code deleted
- The test files of every migrated call site pass, run with the project's per-file
  test command (Endoxia: `pnpm --filter <pkg> test <file>…`); VERIFY's `ship:check`
  selects what they reach. A whole suite needs a stated reason.
- Mark migrate task as completed, move to REVIEW phase
