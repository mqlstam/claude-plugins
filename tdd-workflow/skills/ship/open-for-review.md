# Open for review (ship, phase 1)

This phase puts the reviewed work up as a pull request: it commits, pushes the
branch and opens or updates the PR. Nothing is recorded for the PR and nothing is
attached to it (owner, 2026-11-25); a new or changed UI follows the project's rule
instead (Endoxia: `.claude/rules/browser-monitoring.md`, one short video to the
owner by Slack). Nothing here merges, tags or deploys, so it needs no approval.
VERIFY runs it at READY; `/ship` runs it when the PR is missing or its marker is
stale. Many chats do this in parallel, each for its own branch.

Each command is one plain call with literal values. Stay on this branch.

**Who acts.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run <git push|gh pr ...>`
runs the push and the PR text as the agent's GitHub App when this machine has one
(`${CLAUDE_PLUGIN_ROOT}/docs/APP-SETUP.md`), so the owner can approve the PR on
GitHub, and as you otherwise. It refuses anything but review work (no merge, no
push to main, no tag) and adds the owner as the reviewer of a PR it creates.

## Steps

1. **The verdict covers the tree.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check`
   must exit 0. A refusal goes back to VERIFY; never write around it.

2. **Commit** the reviewed work: stage the relevant files, keep unrelated changes
   out, create a conventional commit; the tree is then clean.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must
   exit 0 (an index staged before the review can commit other contents).

3. **Integrate main only when GitHub reports a conflict.** Opening a PR merges
   nothing in: the merge queue builds the head on current main and proves that
   result. Once the PR exists, read `gh pr view <PR> --json mergeable`; only on
   `CONFLICTING` integrate main as `/ship` does (its "Integrate main" section:
   fetch, `adopt-squash`, the merge with its conventional subject, `verify-state.py
   integrate`), then push again; what runs after it is
   `${CLAUDE_PLUGIN_ROOT}/skills/ship/pre-queue-check.md` section 3. Landing
   directly, with no merge queue, `/ship` integrates and checks the merged tree.

4. **Push the branch:** `verify-state.py check --commit HEAD` again, then
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run git push -u origin <BRANCH>`.
   Never force.

5. **Write the body file**, `.claude/gate-out/pr-body.md` (ignored by git):
   - the summary;
   - `## Validation`, first line the marker that
     `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py review-marker` prints
     (`<!-- verify-verdict: <digest> -->`; `/ship` lands only the head that
     marker's verdict covers), then the pre-queue check that ran, on which commit,
     and its result, what the runtime check drove and read, and each flow it did
     not run as `- NOT RUN: <flow> (<reason>)`. No screenshot, recording or
     attachment.
   - `## After release`: one `- [ ] staging:|production:|both: <step>` line per
     thing this change owes once it reaches that environment (an ops command, a
     backfill, a capability flag), or the heading alone. The session that performs
     a step ticks it with `gh pr edit`.
   Use the PR title the user gave, else a conventional title.

6. **Create the PR, or update the one this branch has.**
   `gh pr view <BRANCH> --json number,state,url` tells which. No open PR:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run gh pr create --title "<title>" --body-file .claude/gate-out/pr-body.md`.
   An open one: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run gh pr edit <PR> --body-file .claude/gate-out/pr-body.md`.
Under the owner's standing approval for `ship` (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-approval-gate.py
--status --session ${CLAUDE_SESSION_ID} --cwd "$PWD"` prints `Standing approval ON` naming ship),
nothing waits for the owner: do steps 7 and 8, then land it at once with `/ship`
(phase 2) in this turn.

7. **Link the PR to the thread.** In T3 Code, `link_pull_request` with the PR URL.
   Then `watch_pull_request` on it, so T3 wakes this chat when the owner reviews or
   comments (or a check fails); end the turn after the report. Without the watch
   tool the owner types `/ship` (or `/approve ship`) in this chat.

8. **Report in one short message:** the PR URL and each NOT RUN with its reason;
   for a new or changed UI, that its video went by the project's rule. Then how to
   approve: an
   approving review on GitHub (when the PR was opened by the agent App), or
   `/ship` in this chat. Nothing merged; production stays `/promote`.

Read main's newest `endoxia/main-health` status with
`gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`
and name a red one in the report, and whether this change fixes it. It never blocks.
