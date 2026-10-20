# Phase 5: VERIFY

A refactor is verified exactly like a feature: read and follow
`${CLAUDE_PLUGIN_ROOT}/skills/feature/phases/04-verify.md`.

Two things to add to the review context:

- **State that the change is a refactor**, and name what it replaces: the old module,
  symbol or path. The most common refactor regression is not in the new code. It is
  what was not deleted: a re-export kept "for compatibility", a `_deprecated` name, a
  caller never migrated, a renamed file that still holds the old body. The reviewer's
  brief checks for this when it is told what was replaced.
- **State the behaviour that must not change.** A refactor that changes nothing
  observable skips runtime verification; one that moves a request path, a persisted
  format or a rendered surface does not.
