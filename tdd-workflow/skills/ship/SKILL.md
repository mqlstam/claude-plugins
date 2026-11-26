---
name: ship
description: Land the pull request the owner reviewed - check it is exactly the reviewed work, integrate main if GitHub needs it, squash-merge that PR head (through GitHub's merge queue where main requires one). It ends when the PR is merged or queued: it deploys nothing and watches no release. Runs when the user types /ship, after /approve ship, when the owner approved the agent App's PR on GitHub, or under the owner's standing approval (`release-approval-gate.py --status` names it; docs/STANDING-APPROVAL.md). Opening the PR for review (phase 1) needs no approval and runs at the end of VERIFY.
release-actions: merge
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(date *), Bash(curl *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_app.py *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`

Each command is one plain call, with literal values. Never pipe a release command.
Stay on this branch; use a feature branch for /ship. Main uses /quickship.

## Two phases, one approval

1. **Open for review**, `${CLAUDE_SKILL_DIR}/open-for-review.md`: commit, push the
   branch, open or update the PR, link it to the thread, tell the owner.
   Nothing merges, so it needs no
   approval: VERIFY runs it at READY, in every chat that builds something.
2. **Land**, the steps below: the merge. It needs the approval, and the builder's
   work ends there.

**The approval** is one of: the typed `/ship`, `/approve ship` as a message of its
own, or (where this machine has the agent App, `${CLAUDE_PLUGIN_ROOT}/docs/APP-SETUP.md`)
the owner's approving review on GitHub of the PR the App opened, or the owner's
standing approval naming `ship` (`${CLAUDE_PLUGIN_ROOT}/docs/STANDING-APPROVAL.md`):
while it is on, nothing waits for the owner. The release gate
reads that review when the merge runs: the owner's newest deciding review is
APPROVED on exactly the PR's head. As on GitHub, a comment after it keeps it; a later
commit, a dismissal or a "changes requested" means no approval. Whichever it is, the approval is spent by the
merge taking effect, never by invoking this skill or by a step or a merge that
failed: retry in this session without asking again. It covers ONE merge. A second
PR, a staging deploy (`/deploy`) and production (`/promote`, `/release`, `/hotfix`)
each need their own approval.

**Woken by T3 on the PR** (the chat watched it in phase 1): read the owner's review,
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_app.py review <PR>`.
- `APPROVED` → land: invoke this skill (the gate allows it on that review). Read
  any comments the owner added after approving too: when one asks for a change,
  say so and ask before landing; praise ("mooi") needs nothing.
- Changes requested, or comments with no approval on the head → read them
  (`gh pr view <PR> --comments`, and `gh api repos/:owner/:repo/pulls/<PR>/comments`
  for the line comments), fix in this chat, VERIFY the change as VERIFY step 4
  says, then open for review again (it updates the PR) and report.
- A failing check → read it (`gh pr checks <PR>`), fix what is this change's, report.
Do not merge on a wake alone: the gate decides from the review, and so do you.

## Steps (land)

1. **Something to ship.** If there is neither a change nor an unmerged commit, STOP.

2. **VERIFY before changing HEAD.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check` must exit 0: the READY
   verdict in `.claude/.verify-state.json` covers exactly the current contents. A
   missing verdict means complete VERIFY through /feature or /refactor first; changed
   contents get a review of the paths it names, then validation. Check it now, before committing and before
   merging main; those steps do not re-arm VERIFY. A new feature change does.

3. **The PR the owner reviewed.** `gh pr view --json number,state,headRefOid,headRefName,url`.
   From here on `<PR>`, `<SHA>` (its head) and `<BRANCH>` are written literally.
   - **No open PR**, or uncommitted reviewed work, or `git rev-parse HEAD` is not
     `<SHA>`: the owner has not seen this. Open for review now
     (`${CLAUDE_SKILL_DIR}/open-for-review.md`). A change with nothing to observe
     then continues here. An observable one STOPS after the report. Their explicit go in
     this chat (`/ship` again, or plainly saying to land it), or their approving
     review, lands it under this same approval, which is still unspent. A question
     or a change request is not a go: work it, and report again.
   - **The head is the reviewed work:**
     `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py review-covers <PR>` must
     exit 0. Exit 1 says which: a commit came after the review (VERIFY it, as VERIFY
     step 4 says), or the PR carries an earlier verdict's marker. Either way say so
     to the owner, open for review again so the PR carries the verdict of what
     lands, and STOP. Never land a head no verdict covers.

4. **Which landing path.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py mode`
   reads main's active rules on GitHub:
   - `0` `merge_queue=required` → land through the queue: "Land through the merge
     queue" below replaces steps 4 to 6, then continue at (7).
   - `1` `merge_queue=none` → land directly, steps 4 to 6 as they stand.
   - `2` → GitHub could not be read: report it and STOP. Never guess a path.

   **Direct: wait for GitHub to settle the PR for `<SHA>`**, in the background. GitHub
   recomputes mergeability after every push; a merge in that window was answered
   "merge conflicts" for a branch without any, and push-to-merge reached a p90 of
   26.6 min. With `run_in_background: true`:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py <PR> --head <SHA>`
   and act on its completion notification (do not poll it, do not `sleep`; its
   completion wakes you even after the turn ended, so carry on from it). It
   counts a state only once `headRefOid` is `<SHA>`, and stops at 180 s (six of
   seven measured settles took at most 26 s). Its last line and exit:
   - `0` `CLEAN` / `HAS_HOOKS` → merge now (5), in the same minute.
   - `6` `UNSTABLE` at the bound → mergeable, but a check that is not required is
     failing or pending: merge (5), and name those checks from `gh pr checks <PR>`.
   - `3` `DIRTY` / `BEHIND` → main must come in first: "Integrate main" below,
     then push the integration as open-for-review step 4 does and set `<SHA>` to
     it, run `review-covers <PR>` again, and wait again. A clean integration keeps
     the verdict. It is a new head, though, so an approval given
     as a GitHub review no longer covers it: tell the owner the PR was brought up
     to date with main and needs their approval again (on GitHub, or `/ship`), and
     STOP. A typed approval covers it: go on.
   - `4` `BLOCKED` → a required check or review is missing: `gh pr checks <PR>`,
     report it, STOP. `--admin` only when the user explicitly asks to force:
     `gh pr merge <PR> --squash --admin --match-head-commit <SHA>`. Never silently.
   - `5` `merge_state_wait_deadline` → report the state it names and STOP; the
     user decides.
   - `7` → the PR is no longer open: go to (6).

   When main moved but merges cleanly, GitHub squash-merges without a new commit on
   the branch, and main's short CI checks the merged range, as for every merge; the
   whole suite gates production. Bringing the branch up to date anyway would cost
   the owner's GitHub approval for no conflict.

5. **Merge:** `gh pr merge <PR> --squash --match-head-commit <SHA>`. No pipe (the
   release gate refuses a piped release: a pipe reports its last command's
   status). No `--delete-branch`: in a worktree gh merges on GitHub, then checks
   out `main` to delete the local branch, fails with `fatal: 'main' is already
   used by worktree at …`, exits 1 after a SUCCESSFUL merge (2026-10-07 and
   10-12), and never reaches the remote branch, which it deletes after the local
   one. `--match-head-commit` makes GitHub refuse if the head moved since (3), and
   it is the head a GitHub review must have approved.

6. **The verdict comes from the PR, never from the merge command's exit code:**
   `gh pr view <PR> --json state,mergeCommit -q '.state + " " + (.mergeCommit.oid // "-")'`.
   `MERGED <sha>` is the verdict; anything else is a failed merge, and the merge
   command's output is the reason. After `MERGED`, delete the remote branch:
   `gh api -X DELETE repos/:owner/:repo/git/refs/heads/<BRANCH>`, the PR's own
   head branch, never `main`/`master` (the release gate refuses deleting main
   through the API). A 422 "Reference does not exist" means the repository's
   "Automatically delete head branches" setting already removed it: done.

7. **Report, and end.** One short message: the PR and `<M>`, or that the PR is
   queued. Deploy nothing, push no tag and watch no release run: staging follows
   main within the hour, and the repository's own workflow does it (it rolls
   main when main moved, and before every promote, watched by one session).
   **Never create or push a `deploy-*` tag:** the staging release writes it
   itself once staging verified the release, so a pushed one starts nothing and
   falsely marks `<M>` as verified. `/deploy` gets main onto staging sooner (a
   dispatch) and keeps its own watch. In T3 Code, settle
   the thread (`t3_thread_organize`, `settle`; it takes effect when the turn ends)
   once the PR is merged. A merge that failed is not a landing: the message STARTS
   with what failed, and the thread is pinned and marked unread instead
   (`t3_thread_organize` with `pin`, then with `mark_unread`).

## Land through the merge queue

Where main requires GitHub's merge queue (step 4 read `merge_queue=required`), the
queue builds each PR on top of main and the PRs ahead of it, runs the required
checks on that merge result, and moves main only when they pass. A PR whose base
moved is not behind any more, so main moving is never a reason to integrate: that
loop is what the queue retires. Same approval, same `<PR>` and `<SHA>`, same step 2
and 3 checks, which already passed.

a. **Settle**, in the background (`run_in_background: true`), acting on its
   completion notification (its completion wakes you; carry on from it in that turn):
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py <PR> --head <SHA> --queue`.
   - `0` `CLEAN` / `HAS_HOOKS` / `BEHIND` → enqueue (b).
   - `3` `DIRTY` → conflicts, which the queue cannot build: "Integrate main" below,
     push as open-for-review step 4 does, set `<SHA>`, `review-covers <PR>` again,
     wait again; the GitHub-review rule of step 4 holds for that new head.
   - `4` `BLOCKED` at the 180 s bound → the PR's own required check or a review is
     missing (`gh pr checks <PR>`). A check still running: wait again. Otherwise
     report it and STOP. GitHub refuses an enqueue until the PR's own requirements
     pass (a known regression, community #190610), so never enqueue before CLEAN.
   - `5`, `6`, `7` → as in step 4.

b. **Enqueue:** `gh pr merge <PR> --squash --match-head-commit <SHA>`, the same
   command as the direct merge: on a queue branch gh adds the PR to the queue
   instead (no `--auto` needed; no `--delete-branch`, which gh refuses there). Its
   output says `will be added to the merge queue for main when ready`, or
   `is already queued to merge`; anything else is a failed enqueue, and the output
   is the reason. `--match-head-commit` still binds it to the reviewed head.

c. **The turn may end once the PR is queued.** Report it queued (7). The queue's
   verdict comes from a background command (`run_in_background: true`), never a
   foreground wait; its completion wakes you, so carry on from it in that turn:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py await <PR> --head <SHA> --slice-min 9`.
   Its bound is derived from the queue: each group ahead and this PR's own may run
   to the queue's check timeout once. Its last line and exit:
   - `0` `merged=<M>` → the queue landed it; `<M>` is the commit on main. Read the
     verdict from the PR as step 6 says (`MERGED <M>`), delete the branch as step 6
     says (usually already gone), and report (7).
   - `6` `merge_queue_still_queued … --until <E>` → run the same command again
     with `--until <E>`, until another exit.
   - `3` `removed` → the PR left the queue without merging. It names the failing
     merge-group run (`merge_group_run=<url>`): read it (`release-run.py reason
     <run-id>`). When the failure is this PR's, fix it on the branch, VERIFY the
     fix as VERIFY step 4 says, open for review again, and land again from (a)
     under the same approval, which the removal did not spend. When it is not
     this PR's (a flaky check, an infrastructure failure), report it and STOP. A
     group that failed for ANOTHER queued PR is rebuilt by GitHub with this one
     still queued: `await` keeps waiting then, it does not exit.
   - `4` `head_moved` → someone pushed to the branch. Its auto-merge may still
     be on, and the PR-side check passes at once, so the unreviewed head could
     enter the queue and merge after you stop: first
     `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py dequeue <PR>`, then STOP
     and report, as for an unreviewed head. A `dequeue` that exits `2` is run
     again once; a second `2` → the report says first that the unreviewed head
     may still land, and names `dequeue <PR>` as the owner's step.
   - `5` `NOT MERGED` → report the entry's state and position it names, and STOP:
     the PR is still queued and nothing is done.
   - `2` → GitHub could not be read: run the same command again (with the
     `--until <E>` it printed, if any). When it answers `2` again, report it and
     STOP: the PR is queued and its approval charged until it merges or leaves.

**Never push to a branch while its PR is queued**: a push evicts it from the queue
(and a later merge would carry an unreviewed head). First
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/merge-queue.py dequeue <PR>`, which takes it
out and turns its auto-merge off; then push, and land again from (a).

## Integrate main

Used by open for review (only when GitHub reports a conflict) and by landing (directly: when GitHub reports
`DIRTY` or `BEHIND`; through the merge queue: `DIRTY` only). `git fetch origin main`, then
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py adopt-squash`, then
`git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
`adopt-squash` records this branch's own squash-merged PRs as merged, but only
when main's squash commit has exactly the PR head's tree, so it changes no file.
Without it the merge replays those PRs against this branch's later edits: PR
#1422 met 82 conflicted paths, 2 of them real. It prints what it adopted, or why
not; exit 1 names what to fix first (staged changes, no fetched main). When main
moved again while landing, run `adopt-squash` and the merge command again, the same
way. Resolve conflicts in this branch, preserving both changes, and finish the
merge commit with that same conventional subject.
Keep commit hooks enabled. Do not rewrite history or force-push.
Right after the merge commit, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py integrate`. Exit 0: the
merge is exactly git's clean merge of the reviewed work and main, and the verdict
covers it without another review. Exit 1 names the paths a conflict resolution or
a hook changed: give them and the contracts they touch a targeted correctness
review, and once it is READY run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py record`.
A new verdict means a new marker: run the runtime criteria the resolved paths touch
again (or name each flow NOT RUN with its reason), open for review again, and STOP
for the owner; `review-covers` refuses the old marker.
Substantive new feature work re-arms VERIFY fully.

**Local validation.** The pre-queue check decides it
(`${CLAUDE_PLUGIN_ROOT}/skills/ship/pre-queue-check.md` section 3). **Through the merge
queue** (`merge-queue.py mode` exit `0`), a clean or conflict-only integration
runs no new check: VERIFY's check of the reviewed work stands, and the queue's
required check proves the merge result before main moves. A resolution that wrote
new code is a fix: VERIFY step 4. **Directly** (`merge_queue=none`), nothing else
proves the merge result, so read the checkout's own `package.json` after the merge
and run its check once on the merged tree. When it declares `precheck`, run
`pnpm precheck`. Otherwise, when it declares `ship:check`, run `pnpm ship:check`. It
already ran in VERIFY; this second run reuses each pass whose input contents are
byte-identical, so a no-op or docs-only merge re-runs only what the merge reaches.
On that path never skip it, and never replace it with commands picked by hand. It
selects affected lint/typecheck, scoped assertions and directly touched tests. The
model may add `--add-tests`, `--add-package` or `--add-checks`, each with
`--reason`, for a boundary the automatic selection cannot see; nothing narrows the
floor.
Exit `0` passes, `1` fails, `2` refuses (fix the named environment issue, then
retry). Any other nonzero exit or interrupted command is not a pass: report its
output and resolve the cause. No automatic full-suite escalation.
Without either script, follow `${CLAUDE_SKILL_DIR}/generic.md` (the repo's own
lint, typecheck, formatting, tests, build and assertions).
Independent checks may run together. A call that can exceed about 8 minutes
runs with `run_in_background: true`, never sleep-polled; its completion wakes you, so carry the workflow on from it in that turn.
Any validation failure blocks this ship.

Before every push, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must exit 0, so
nothing a hook or a later commit added goes out unreviewed. When the PR's
`headRefOid` differs from the commit you pushed, someone else pushed: fetch that
commit and run `verify-state.py check --commit` on it; adopt it only when that
exits 0, otherwise STOP and report the unreviewed head.

Main's newest `endoxia/main-health` status:
`gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
A red main never blocks a ship: print the failing status and say whether this
change fixes it. Missing or pending health is reported too; an API error means
unknown health and does not block this ship. Through a merge queue a merge can no
longer turn main red by a semantic conflict with another PR: the queue proves the
merge result before main moves.

## After it lands

1. **Stop what this worktree started, in this order: dev server, Chrome, containers.**
   Stop BEFORE deleting build output: `rm -rf packages/web/.next` under a live
   `next dev` corrupts the server's state. Each entry point is pidfile-scoped and
   idempotent, so calling it when nothing runs is a no-op.

   ```bash
   bash scripts/wt.sh ./scripts/worktree-dev-detached.sh --stop 2>/dev/null || true
   bash scripts/wt.sh ./scripts/worktree-chrome.sh --stop 2>/dev/null || true
   bash scripts/wt.sh docker compose stop 2>/dev/null || true
   ```

2. **Delete only `packages/web/.next`** (a stale one has skewed a bundle budget and
   filled the disk). Never `packages/*/dist`: `pnpm env:drift` and the checks read
   `packages/shared/dist`, and a worktree stripped of it cannot run its next gate.

   ```bash
   rm -rf packages/web/.next
   ```

3. **Do not remove the worktree, and do not tell the user to remove it from another
   terminal.** Leaving is one step for the user: exit this session (Ctrl-C) and
   choose "Remove worktree"; the plugin's SessionEnd hook then retires what the
   worktree left outside its checkout (stack, volumes, images, dev server, Chrome)
   via the repo's `scripts/worktree-retire.sh`. A kept worktree is removed by the
   session-start sweep once its work is on main, it is clean and unused for a week.
   Say this in one line.

   **In a T3 Code thread** (the session runs in `~/.t3/worktrees/...`, or
   `CLAUDE_CODE_ENTRYPOINT` is `sdk-*`) there is no exit prompt: step 7 settles
   the thread of a merged PR. Where the repo reads T3's state (Endoxia,
   slice 752) the session-start sweep then stops its stack and removes its
   checkout when it is clean and its work is on main. Say that instead.

**Landing does not deploy, and this skill starts no deploy.** The builder ends at
the merge. A deploy starts only on a dispatch of the deploy workflow, never on a
push to `main` or of a tag. Staging follows main within the hour (the
repository's own workflow dispatches it when main moved, and before every
promote, watched by one session); `/deploy` is the dispatch by hand for when
staging must have main sooner, and production is a separate `/promote`.
