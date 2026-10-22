---
name: ship
description: Validate the change, commit, merge main, push, open a PR, squash-merge directly, then deploy the merge commit to staging. Runs when the user types /ship, or after /approve ship; that one approval covers the merge and the staging deploy of what it merged.
release-actions: merge
release-follow-on: deploy-tag
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(date *), Bash(curl *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`

Each command is one plain call, with literal values. Never pipe a release command.
Stay on this branch; use a feature branch for /ship. Main uses /quickship.

**The approval.** This run's approval (the typed `/ship`, or `/approve ship`) is
spent by the merge taking effect, never by invoking this skill or by a step or a
merge that failed: retry in this session without asking again. It covers ONE merge
and then ONE staging deploy tag on exactly the merge commit it produced (step 8).
A second PR, a tag on any other commit, and production (`/promote`, `/release`,
`/hotfix`) each need their own approval.

## Steps

1. **Something to ship.** If there is neither a change nor an unmerged commit, STOP.

2. **VERIFY before changing HEAD.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check` must exit 0: the READY
   verdict in `.claude/.verify-state.json` covers exactly the current contents. A
   missing verdict means complete VERIFY through /feature or /refactor first; changed
   contents get a review of the paths it names, then validation. Check it now, before committing and before
   merging main; those steps do not re-arm VERIFY. A new feature change does.

3. **Commit.** Stage relevant files and create a conventional commit. Read the diff
   first and keep unrelated changes out. The working tree must then be clean.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must
   then exit 0: the commit holds exactly the reviewed contents. An index staged
   before the review can commit other contents than the files on disk, which the
   working-tree check in step 2 cannot see. On a refusal, review the paths it names.

4. **Merge main in.** `git fetch origin main`, then
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py adopt-squash`, then
   `git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
   `adopt-squash` records this branch's own squash-merged PRs as merged, but only
   when main's squash commit has exactly the PR head's tree, so it changes no file.
   Without it the merge replays those PRs against this branch's later edits: PR
   #1422 met 82 conflicted paths, 2 of them real. It prints what it adopted, or why
   not; exit 1 names what to fix first (staged changes, no fetched main).
   Resolve conflicts in this branch, preserving both changes, and finish the merge
   commit with that same conventional subject.
   Keep commit hooks enabled. Do not rewrite history or force-push.
   Right after the merge commit, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py integrate`. Exit 0: the
   merge is exactly git's clean merge of the reviewed work and main, and the verdict
   covers it without another review. Exit 1 names the paths a conflict resolution or
   a hook changed: give them and the contracts they touch a targeted correctness
   review, and once it is READY run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py record`.
   Substantive new feature work re-arms VERIFY fully. Local validation below checks
   the merged tree.

5. **Local validation.** Read the checkout's own `package.json` after the merge.
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

6. **Push and open the PR.** Before every push, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must exit 0, so
   nothing a hook or a later commit added goes out unreviewed. Then
   `git push -u origin <branch>`, then
   `gh pr create --title "<title>" --body-file <body-file>`. Write the summary and
   validation evidence to that file first, ending with `## After release`: one
   `- [ ] staging:|production:|both: <step>` line per thing this change owes once it
   reaches that environment (an ops command, a backfill, a capability flag), or the
   heading alone when nothing is owed. `--body-file` bypasses the PR template, so
   write it yourself; the session that performs a step ticks it with `gh pr edit`. Use $ARGUMENTS as title when supplied.
   **The runtime evidence goes on the PR**, so the owner reviews the change there.
   When `.claude/verify-evidence/manifest.json` exists (VERIFY writes it at READY
   when runtime verification was in scope), add an `## Evidence` section above
   `## After release`: per item its criterion, its caption, and a reference to its
   path written as `./<path>`, an image as `![<caption>](./<path>)` and a recording
   as `./<path>` alone on its own line (GitHub plays a video only there), then each
   `notRun` entry as `- NOT RUN: <criterion> (<reason>)`. Give `gh pr create` one
   `--attach ./<path>` per item, with exactly the path the body references; gh
   uploads each file and rewrites that reference to the uploaded asset. gh takes at
   most 50 files per command: with more items, attach the first 50 to `gh pr create`,
   then for each further batch of up to 50 write the PR's current body
   (`gh pr view <PR> --json body -q .body`) to the body file and run
   `gh pr edit <PR> --body-file <body-file> --attach ./<path> ...`, so that batch's
   references are rewritten and the earlier ones keep their uploaded links. Images up to
   10 MB and recordings up to 100 MB upload; a larger file is named in the section
   as not attached. When gh exits nonzero after printing a PR URL, the PR exists
   with the files that did upload: do not create another, run
   `gh pr edit <PR> --attach ./<path>` for the missing ones, and name any that still
   fail. A gh without `--attach` (before 2.99) gets the section without the files,
   and the report says so. Without a manifest, the section says why there is no
   runtime evidence (out of scope, or NOT RUN with its reason).
   Read main's newest `endoxia/main-health` status with
   `gh api repos/:owner/:repo/commits/main/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
   A red main never blocks a ship: print the failing status and say whether this
   change fixes it. Missing or pending health is reported too; an API error means
   unknown health and does not block this ship. Main's short CI
   checks the merged range after the merge; the whole suite gates production.

7. **Squash-merge once GitHub has settled the PR for the commit you pushed.**
   GitHub recomputes mergeability in the background after every push. A merge in
   that window was answered "merge conflicts" for a branch without any, the session
   merged main again and re-ran the gate, and push-to-merge reached a p90 of
   26.6 min. Every command below is one plain command with the values written out.

   a. **Read the PR:** `gh pr view --json number,headRefOid,headRefName`. From here
      on `<PR>`, `<SHA>` and `<BRANCH>` are written literally. `<SHA>` is the commit you
      checked and pushed (`git rev-parse HEAD`). When the PR's `headRefOid` differs from it, someone else pushed: fetch
      that commit and run `verify-state.py check --commit` on it; adopt it only when
      that exits 0, otherwise STOP and report the unreviewed head.

   b. **Wait for the settled state, in the background.** With
      `run_in_background: true`:
      `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py <PR> --head <SHA>`
      and act on its completion notification (do not poll it, do not `sleep`). It
      counts a state only once `headRefOid` is `<SHA>`, and stops at 180 s (six of
      seven measured settles took at most 26 s). Its last line and exit:
      - `0` `CLEAN` / `HAS_HOOKS` → merge now (c), in the same minute.
      - `6` `UNSTABLE` at the bound → mergeable, but a check that is not required is
        failing or pending: merge (c), and name those checks from
        `gh pr checks <PR>` in your report.
      - `3` `DIRTY` / `BEHIND` → `git fetch origin main`, then
        `git rev-list --count HEAD..origin/main`. `0` means the state was stale: run
        the wait again. Otherwise main moved: run `adopt-squash` and the merge command
        from step 4 with its conventional subject, resolve conflicts, run `verify-state.py integrate`
        as in step 4, run local validation again, check the commit as in step 6, then
        push and wait again.
      - `4` `BLOCKED` → a required check or review is missing: `gh pr checks <PR>`,
        report it, STOP. `--admin` only when the user explicitly asks to force:
        `gh pr merge <PR> --squash --admin --match-head-commit <SHA>`. Never silently.
      - `5` `merge_state_wait_deadline` → report the state it names and STOP; the
        user decides.
      - `7` → the PR is no longer open: go to (d).

   c. **Merge:** `gh pr merge <PR> --squash --match-head-commit <SHA>`. No pipe (the
      release gate refuses a piped release: a pipe reports its last command's
      status). No `--delete-branch`: in a worktree gh merges on GitHub, then checks
      out `main` to delete the local branch, fails with `fatal: 'main' is already
      used by worktree at …`, exits 1 after a SUCCESSFUL merge (2026-10-07 and
      10-12), and never reaches the remote branch, which it deletes after the local
      one. `--match-head-commit` makes GitHub refuse if the head moved since (b).

   d. **The verdict comes from the PR, never from the merge command's exit code:**
      `gh pr view <PR> --json state,mergeCommit -q '.state + " " + (.mergeCommit.oid // "-")'`.
      `MERGED <sha>` is the verdict; anything else is a failed merge, and the merge
      command's output is the reason.

   e. **Delete the remote branch, after `MERGED` only:**
      `gh api -X DELETE repos/:owner/:repo/git/refs/heads/<BRANCH>`, the PR's own
      head branch, never `main`/`master` (the release gate refuses deleting main
      through the API). A 422 "Reference does not exist" means the repository's
      "Automatically delete head branches" setting already removed it: done.

8. **Deploy what you merged to staging, under this same approval.** `<M>` is the
   merge commit from (d). First, whether the project deploys at all:
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

   Report the staging result as the deploy steps say. Production stays untouched
   until `/promote`.


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
   `CLAUDE_CODE_ENTRYPOINT` is `sdk-*`) there is no exit prompt: settle the thread
   in T3 (T3 also settles it once the PR merged). Where the repo reads T3's state
   (Endoxia, slice 752) the session-start sweep then stops its stack and removes
   its checkout when it is clean and its work is on main. Say that instead.

**Landing alone does not deploy.** A deploy fires only on a `deploy-*` tag (or a
manual dispatch), never on a push to `main`. Step 8 pushes that tag for the merge
commit, to STAGING where the project has one (Endoxia, slice 603), under this
ship's approval; production is a separate `/promote`.
