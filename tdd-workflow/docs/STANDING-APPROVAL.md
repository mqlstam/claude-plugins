# The owner's standing approval

Owner, 2026-10-28: "geen stop, full send". One file on the machine turns the
per-release approval off for the release skills it names. While it exists, Claude
invokes those skills and runs their release commands (merge, push to main, deploy
tag, workflow dispatch, a re-run of a failed run) without a typed `/approve`, and
VERIFY lands its PR with `/ship` right after opening it.

    ~/.config/claude-tdd-workflow/release-approval.json   (or $CLAUDE_TDD_RELEASE_APPROVAL_CONFIG)

    {
      "standing": ["ship", "quickship", "merge", "deploy", "promote", "release", "hotfix"],
      "since": "2026-10-28T21:00Z",
      "note": "owner: full send"
    }

- `standing` is the only key the gate reads: a non-empty list of release skill
  names. Leave a skill out (say `promote`, `release`, `hotfix` for production) and
  that skill needs `/approve` again.
- The file must belong to the user and must not be writable by others
  (`chmod 600`). An unreadable, malformed or loose file approves nothing, and
  `release-approval-gate.py --status` names the problem.
- The gate reads the file on every call. Delete it, and the per-release approval is
  back at once, in every running session.
- What stays while it is on: one release per command, no pipe after a release
  command, releases only in the main conversation (never a subagent), and an agent
  never writes a pull request review.
- The file is the owner's. An agent never creates, edits or deletes it.
