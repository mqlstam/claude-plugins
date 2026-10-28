# Open for review (ship, phase 1)

The owner approves a change by watching its evidence on the pull request, BEFORE it
merges (owner, 2026-10-22). This phase puts the reviewed work up for that: it
commits, integrates main, pushes the branch and opens or updates the PR with the
runtime evidence. Nothing here merges, tags or deploys, so it needs no approval.
VERIFY runs it at READY; `/ship` runs it when the PR is missing or its evidence is
stale. Many chats do this in parallel, each for its own branch.

Each command is one plain call with literal values. Stay on this branch.

**Who acts.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run <git push|gh pr ...>`
runs the push and the PR text as the agent's GitHub App when this machine has one
(`${CLAUDE_PLUGIN_ROOT}/docs/APP-SETUP.md`), so the owner can approve the PR on
GitHub, and as you otherwise. It refuses anything but review work (no merge, no
push to main, no tag) and adds the owner as the reviewer of a PR it creates.
Attachments are uploaded with your own `gh`: gh refuses an App's token for them.

## Steps

1. **The verdict covers the tree.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check`
   must exit 0, and `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py evidence`
   must exit 0: the manifest names every user flow of an observable change with its
   recording or its NOT RUN reason, or says why the change has nothing to observe.
   A refusal goes back to VERIFY; never write around it.

2. **Commit** the reviewed work: stage the relevant files, keep unrelated changes
   out, create a conventional commit; the tree is then clean.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must
   exit 0 (an index staged before the review can commit other contents).

3. **Integrate main**, as `/ship` does (its "Integrate main" section): fetch,
   `adopt-squash`, the merge with its conventional subject, `verify-state.py
   integrate`, then local validation on the merged tree. The owner then reviews
   the change as it would land, and a PR that is already current needs no new
   commit, and so no new approval, when it lands.

4. **Push the branch:** `verify-state.py check --commit HEAD` again, then
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run git push -u origin <BRANCH>`.
   Never force.

5. **Write the body file**, `.claude/verify-evidence/pr-body.md` (ignored by git):
   - the summary and the validation evidence (what ran, the mutation record);
   - `## Evidence`, from `.claude/verify-evidence/manifest.json`, first line the
     marker that `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py review-marker`
     prints (`<!-- verify-verdict: <digest> -->`; `/ship` lands only the head that
     marker's verdict covers). Then, for an observable change: per user flow its
     recording as `./<path>` alone on its own line (GitHub plays a video only
     there), with its criterion and caption; per criterion its screenshot as
     `![<caption>](./<path>)`; and each `notRun` entry as
     `- NOT RUN: <criterion> (<reason>)`. For a change with nothing to observe:
     `Runtime evidence: out of scope (<outOfScope>)`.
   - `## After release`: one `- [ ] staging:|production:|both: <step>` line per
     thing this change owes once it reaches that environment (an ops command, a
     backfill, a capability flag), or the heading alone. The session that performs
     a step ticks it with `gh pr edit`.
   Use the PR title the user gave, else a conventional title.

6. **Create the PR, or update the one this branch has.**
   `gh pr view <BRANCH> --json number,state,url` tells which. No open PR:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run gh pr create --title "<title>" --body-file .claude/verify-evidence/pr-body.md`.
   An open one: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py run gh pr edit <PR> --body-file .claude/verify-evidence/pr-body.md`.
   Either way the body still holds `./<path>` references.

7. **Attach the evidence:**
   `gh pr edit <PR> --body-file .claude/verify-evidence/pr-body.md --attach ./<path> ...`,
   one `--attach` per item with exactly the path the body references (gh >= 2.99;
   gh uploads each file and rewrites its reference to the uploaded asset). At most
   50 files per command: for each further batch write the PR's current body
   (`gh pr view <PR> --json body -q .body`) to the body file first, so the earlier
   references keep their uploaded links. Images up to 10 MB and recordings up to
   100 MB upload; a larger file is named in the section as not attached. When gh
   exits nonzero, the files that uploaded stay: attach the missing ones again and
   name any that still fail. A gh without `--attach` leaves the references
   unplayable: say so in the report, never silently.

Under the owner's standing approval for `ship` (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-approval-gate.py
--status --session ${CLAUDE_SESSION_ID} --cwd "$PWD"` prints `Standing approval ON` naming ship),
nothing waits for the owner: do steps 8 and 9, then land it at once with `/ship`
(phase 2) in this turn. The evidence stays on the PR for the owner to watch later.

8. **Link the PR to the thread.** In T3 Code, `link_pull_request` with the PR URL.
   Then `watch_pull_request` on it, so T3 wakes this chat when the owner reviews or
   comments (or a check fails); end the turn after the report. Without the watch
   tool the owner types `/ship` (or `/approve ship`) in this chat.

9. **Report in one short message:** the PR URL, and what to watch: each recording
   by its flow, and each NOT RUN with its reason. Then how to approve: an
   approving review on GitHub (when the PR was opened by the agent App), or
   `/ship` in this chat. Nothing merged; production stays `/promote`.

Read main's newest `endoxia/main-health` status with
`gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`
and name a red one in the report, and whether this change fixes it. It never blocks.
