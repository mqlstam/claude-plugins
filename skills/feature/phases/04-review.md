# Phase 4: REVIEW (Subagent)

## Goal
Independent code review of all changes. Fixes issues in place, flags what needs human attention.

## Agent
Delegate to the `slice-reviewer` agent to review all changes made in this feature.

## What the reviewer does
1. Scopes the diff (changed + new files)
2. Runs review checklist:
   - Code quality (unused imports, bare TODOs, console.log, type safety)
   - CLAUDE.md compliance (marker conventions, guardrails)
   - Test quality (assertions meaningful, edge cases covered)
   - Security (injection, tenant isolation, credential handling)
   - Architecture (dependency direction, layer boundaries)
3. Fixes trivial issues in place (unused imports, formatting, bare TODOs → proper markers)
4. Flags non-trivial issues needing human attention
5. Produces verdict: **READY** | **NEEDS ATTENTION** | **NEEDS WORK**

## After the reviewer returns
- **READY**: proceed to INTEGRATION
- **NEEDS ATTENTION**: review flagged items with user, decide to address or accept, then proceed
- **NEEDS WORK**: fix critical issues, re-run the reviewer

## Completion
- Review verdict received
- Critical issues addressed
- Mark review task as completed, move to INTEGRATION phase
