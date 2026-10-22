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
  observable is out of scope for runtime verification, and its manifest says why
  (`observable: false`); one that moves a request path, a persisted format or a
  rendered surface is observable, and records each user flow it moves, so the owner
  sees on the PR that it still works.

At READY it opens the PR for review the same way (`skills/ship/open-for-review.md`).
