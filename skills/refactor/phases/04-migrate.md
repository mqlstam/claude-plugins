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
   - Runs module tests
   - Reports completion

3. After ALL modules migrated:
   - Delete old code entirely
   - Remove old tests
   - Clean up unused imports
   - No backward compatibility — the old version is gone completely

## Completion
- All callers migrated
- Old code deleted
- All tests pass: `npm run test`
- Mark migrate task as completed, move to REVIEW phase
