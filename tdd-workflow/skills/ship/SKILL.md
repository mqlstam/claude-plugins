---
name: ship
description: Land the pull request the owner reviewed - check it is exactly the reviewed work with that review's evidence, integrate main if GitHub needs it, squash-merge that PR head, then deploy the merge commit to staging and watch it to its verdict. Runs when the user types /ship, after /approve ship, or when the owner approved the agent App's PR on GitHub; that one approval covers the merge and the staging deploy of what it merged. Opening the PR for review (phase 1) needs no approval and runs at the end of VERIFY.
release-actions: merge
release-follow-on: deploy-tag
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(date *), Bash(curl *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_app.py *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`

Each command is one plain call, with literal values. Never pipe a release command.
Stay on this branch; use a feature branch for /ship. Main uses /quickship.

## Two phases, one approval

1. **Open for review**, `${CLAUDE_SKILL_DIR}/open-for-review.md`: commit, integrate
   main, push the branch, open or update the PR with the runtime evidence, link it
   to the thread, tell the owner what to watch. Nothing merges, so it needs no
   approval: VERIFY runs it at READY, in every chat that builds something.
2. **Land**, the steps below: the merge and the staging deploy. They need the
   approval.

**The approval** is one of: the typed `/ship`, `/approve ship` as a message of its
own, or (where this machine has the agent App, `${CLAUDE_PLUGIN_ROOT}/docs/APP-SETUP.md`)
the owner's approving review on GitHub of the PR the App opened. The release gate
reads that review when the merge runs: the owner's newest deciding review is
APPROVED on exactly the PR's head. As on GitHub, a comment after it keeps it; a later
commit, a dismissal or a "changes requested" means no approval. Whichever it is, the approval is spent by the
merge taking effect, never by invoking this skill or by a step or a merge that
failed: retry in this session without asking again. It covers ONE merge and then
ONE staging deploy tag on exactly the merge commit it produced (step 7). A second
PR, a tag on any other commit, and production (`/promote`, `/release`, `/hotfix`)
each need their own approval.

**Woken by T3 on the PR** (the chat watched it in phase 1): read the owner's review,
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_app.py review <PR>`.
- `APPROVED` → land: invoke this skill (the gate allows it on that review). Read
  any comments the owner added after approving too: when one asks for a change,
  say so and ask before landing; praise ("mooi") needs nothing.
- Changes requested, or comments with no approval on the head → read them
  (`gh pr view <PR> --comments`, and `gh api repos/:owner/:repo/pulls/<PR>/comments`
  for the line comments), fix in this chat, VERIFY the change as VERIFY step 4
  says, then open for review again (it updates the PR and its evidence) and report.
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
     (its manifest says out of scope) then continues here. An observable one STOPS
     after the report: the owner watches the evidence first. Their explicit go in
     this chat (`/ship` again, or plainly saying to land it), or their approving
     review, lands it under this same approval, which is still unspent. A question
     or a change request is not a go: work it, and report again.
   - **The head is the reviewed work, with that review's evidence:**
     `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py review-covers <PR>` must
     exit 0. Exit 1 says which: a commit came after the review (VERIFY it, as VERIFY
     step 4 says), or the PR shows an earlier VERIFY's evidence. Either way say so
     to the owner, open for review again so the PR shows the evidence of what
     lands, and STOP: the owner watches it before it lands. Never land a head the
     owner has not been shown.

4. **Wait for GitHub to settle the PR for `<SHA>`**, in the background. GitHub
   recomputes mergeability after every push; a merge in that window was answered
   "merge conflicts" for a branch without any, and push-to-merge reached a p90 of
   26.6 min. With `run_in_background: true`:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py <PR> --head <SHA>`
   and act on its completion notification (do not poll it, do not `sleep`). It
   counts a state only once `headRefOid` is `<SHA>`, and stops at 180 s (six of
   seven measured settles took at most 26 s). Its last line and exit:
   - `0` `CLEAN` / `HAS_HOOKS` → merge now (5), in the same minute.
   - `6` `UNSTABLE` at the bound → mergeable, but a check that is not required is
     failing or pending: merge (5), and name those checks from `gh pr checks <PR>`.
   - `3` `DIRTY` / `BEHIND` → main must come in first: "Integrate main" below,
     then push the integration as open-for-review step 4 does and set `<SHA>` to
     it, run `review-covers <PR>` again, and wait again. A clean integration keeps
     the verdict and its evidence. It is a new head, though, so an approval given
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

7. **Deploy what you merged to staging, under this same approval, and watch it to
   its verdict in this turn.** `<M>` is the merge commit from (6). First, whether
   the project deploys at all:
   `gh api repos/:owner/:repo/actions/workflows/deploy.yml --jq .state`. A 404 (no
   such workflow) means there is no staging deploy here: say so in one line and go
   on to "After it lands".

   Otherwise read `${CLAUDE_PLUGIN_ROOT}/skills/deploy/SKILL.md` and carry out its
   steps here, with `<SHA>` = `<M>`, not main's tip. Do not invoke `/deploy`
   through the Skill tool: it would tag main's tip, and this approval covers
   exactly one `deploy-*` tag on `<M>`; a tag on any other commit needs
   `/approve deploy`. Create the tag on `<M>` before pushing it, then push it by
   its literal name in a command of its own: the gate reads the tag's commit.

   Before tagging, make sure staging does not already serve a commit that contains
   `<M>` (a tag on `<M>` would roll it back, e.g. after another session's `/release`
   of a newer main). Read what
   staging SERVES: `gh api repos/:owner/:repo/environments/staging/variables/PUBLIC_URL --jq .value`
   gives `<URL>`, and `curl --max-time 10 <URL>/api/health/live` answers JSON whose
   `version` is the served commit `<SERVED>`. When either cannot be read, push NO
   tag: no other source says what staging serves (the newest successful deploy
   run may be a production-only `/hotfix`), and a tag on an older commit would
   roll staging back. Report "staging deploy NOT RUN: staging's health route did
   not answer" with `<URL>` (or that it could not be read) and `<M>`, and name
   `/deploy` as the owner's follow-up. Otherwise
   `git merge-base --is-ancestor <M> <SERVED>` (exit `128`: `git fetch --quiet origin <SERVED>`
   first) exiting `0` means staging already serves `<M>`: report that and push
   nothing.

   **The turn does not end before the deploy run's verdict.** The deploy steps wait
   for it in foreground slices (`release-run.py await`). A thread can settle at the
   end of the turn its PR merged in (T3's settle-on-merge): on 2026-10-20 that
   killed a background await, and a failed run went unseen for 9.5 h.
   Production stays untouched until `/promote`.

8. **Report, and let the owner see what needs them.**
   - **Green** (staging verified, or no staging deploy here): one short message,
     the PR, `<M>`, and the staging verdict. In T3 Code, then settle the thread
     (`t3_thread_organize`, `settle`; it takes effect when the turn ends).
   - **Red, NOT RUN or not concluded**: the message STARTS with that (what failed,
     the run URL, `<M>`), then what the owner can do (`/deploy`,
     the project's release status command such as `pnpm release:status`). In T3
     Code, do not settle: `t3_thread_organize` with `pin`, then with `mark_unread`,
     so the thread stays on top of the owner's list and unread. Where T3 settles a
     thread when its PR merges whatever the thread says (its settle-on-merge
     setting), the thread may still leave the list: say so in the message, and that
     the release status names the open run.

## Integrate main

Used by open for review (always) and by landing (when GitHub reports `DIRTY` or
`BEHIND`). `git fetch origin main`, then
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
A new verdict means new evidence: record the runtime evidence for the resolved
paths again (or name each flow NOT RUN with its reason) with the new `verdictId`, open
for review again, and STOP for the owner; `review-covers` refuses the old manifest.
Substantive new feature work re-arms VERIFY fully.

**Local validation.** Read the checkout's own `package.json` after the merge.
When it declares `ship:check`, run `pnpm ship:check` once. It already ran in
VERIFY; this second run reuses each pass whose input contents are byte-identical,
so a no-op or docs-only merge re-runs only what the merge reaches. Never skip it,
and never replace it with commands picked by hand. It selects affected
lint/typecheck, scoped assertions and directly touched tests. The model may add
`--add-tests`, `--add-package` or `--add-checks`, each with `--reason`, for a
boundary the automatic selection cannot see; nothing narrows the floor.
Exit `0` passes, `1` fails, `2` refuses (fix the named environment issue, then
retry). Any other nonzero exit or interrupted command is not a pass: report its
output and resolve the cause. No automatic full-suite escalation.
Without that script, follow `${CLAUDE_SKILL_DIR}/generic.md` (the repo's own
lint, typecheck, formatting, tests, build and assertions).
Independent checks may run together. A call that can exceed about 8 minutes
runs with `run_in_background: true`; wait for its completion notification,
never sleep-poll. Any validation failure blocks this ship.

Before every push, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must exit 0, so
nothing a hook or a later commit added goes out unreviewed. When the PR's
`headRefOid` differs from the commit you pushed, someone else pushed: fetch that
commit and run `verify-state.py check --commit` on it; adopt it only when that
exits 0, otherwise STOP and report the unreviewed head.

Main's newest `endoxia/main-health` status:
`gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
A red main never blocks a ship: print the failing status and say whether this
change fixes it. Missing or pending health is reported too; an API error means
unknown health and does not block this ship.

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
   `CLAUDE_CODE_ENTRYPOINT` is `sdk-*`) there is no exit prompt: step 8 settles a
   green thread and keeps a red one. Where the repo reads T3's state (Endoxia,
   slice 752) the session-start sweep then stops its stack and removes its
   checkout when it is clean and its work is on main. Say that instead.

**Landing alone does not deploy.** A deploy fires only on a `deploy-*` tag (or a
manual dispatch), never on a push to `main`. Step 7 pushes that tag for the merge
commit, to STAGING where the project has one (Endoxia, slice 603), under this
ship's approval; production is a separate `/promote`.
