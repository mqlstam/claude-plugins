---
name: quickship
description: Validate, commit, merge origin/main and push directly to main without a PR, then deploy the pushed commit to staging. Runs when the user types /quickship, or after /approve quickship; that one approval covers the push and the staging deploy of what it pushed.
release-actions: push-main
release-follow-on: deploy-tag
argument-hint: "[commit message]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(date *), Bash(curl *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py *)
---

# Quick Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Worktree: !`git rev-parse --show-toplevel`
- Worktrees: !`git worktree list --porcelain`

**The approval.** This run's approval (the typed `/quickship`, or
`/approve quickship`) is spent by the push landing on `main`, never by invoking
this skill or by a push that was rejected: retry in this session without asking
again. It covers ONE push to `main` and then ONE staging deploy tag on exactly the
commit that push put there (step 8). Production (`/promote`, `/release`,
`/hotfix`) needs its own approval.

1. **Only main, in the primary checkout.** Otherwise STOP and use /ship from the
   feature branch. Never switch branches in a shared tree. If nothing changed, STOP.
2. **VERIFY before changing HEAD.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check` must exit 0: the READY
   verdict in `.claude/.verify-state.json` covers exactly the current contents.
   Otherwise complete VERIFY first, or review the changed paths it names.
   Check before committing and merging main, so integration does not re-arm VERIFY.
3. **Commit.** Stage relevant changes and create a conventional commit, using
   $ARGUMENTS when supplied. Keep unrelated changes out.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must
   then exit 0: the commit holds exactly the reviewed contents. An index staged
   before the review can commit other contents than the files on disk, which the
   working-tree check in step 2 cannot see. On a refusal, review the paths it names.
4. **Integrate.** `git fetch origin main`, then
   `git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`.
   Resolve conflicts and finish the merge with that same conventional subject.
   Right after the merge commit, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py integrate`. Exit 0: the
   merge is exactly git's clean merge of the reviewed work and main, and the verdict
   covers it without another review. Exit 1 names the paths a conflict resolution or
   a hook changed: give them and the contracts they touch a targeted correctness
   review, and once it is READY run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py record`.
   The same holds for every later integration retry.
   Keep commit hooks enabled. Never rewrite history or force-push.
5. **Validate the merged tree.** Read this checkout's own `package.json` after
   merging. With `ship:check`, run `pnpm ship:check`; add tests, packages or checks
   only with a reason, never narrow its floor. Exit `0` passes; `1` fails; `2`
   refuses the environment. Resolve the named cause; no other exit is a pass.
   Without the script follow `${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`.
   Background calls that can exceed about 8 minutes and wait for notification.
6. **Report main health.** Read the newest `endoxia/main-health` status on origin's
   main tip; print red, pending or missing status and whether this change fixes a
   red. A red main does not block this push.
7. **Push.** Before every push, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py check --commit HEAD` must exit 0, so
   nothing a hook or a later commit added goes out unreviewed. Then `git push origin main`,
   alone and unredirected: the release gate reads the commit it pushed from its
   `<old>..<new>  main -> main` line. A non-fast-forward rejection means main
   moved: fetch, merge, `integrate` and validate again, then retry. Confirm the
   pushed commit `<P>` (`git rev-parse HEAD`, the `<new>` of that line).
8. **Deploy what you pushed to staging, under this same approval.** First, whether
   the project deploys at all: `gh api repos/:owner/:repo/actions/workflows/deploy.yml --jq .state`.
   A 404 (no such workflow) means there is no staging deploy here: say so in one
   line and stop. Otherwise read `${CLAUDE_PLUGIN_ROOT}/skills/deploy/SKILL.md` and
   carry out its steps here, with `<SHA>` = `<P>`. Do not invoke `/deploy` through
   the Skill tool: this approval covers exactly one `deploy-*` tag on `<P>` while
   `<P>` is on origin's `main`; a tag on any other commit needs `/approve deploy`.
   Create the tag on `<P>` before pushing it, then push it by its literal name in a
   command of its own. Before tagging, make sure staging does not already serve a
   commit that contains `<P>` (a tag on `<P>` would roll it back). Read what
   staging SERVES: `gh api repos/:owner/:repo/environments/staging/variables/PUBLIC_URL --jq .value`
   gives `<URL>`, and `curl --max-time 10 <URL>/api/health/live` answers JSON whose
   `version` is the served commit `<SERVED>`. When either cannot be read, push NO
   tag: no other source says what staging serves (the newest successful deploy
   run may be a production-only `/hotfix`), and a tag on an older commit would
   roll staging back. Report "staging deploy NOT RUN: staging's health route did
   not answer" with `<URL>` (or that it could not be read) and `<P>`, and name
   `/deploy` as the owner's follow-up. Otherwise
   `git merge-base --is-ancestor <P> <SERVED>` (exit `128`: `git fetch --quiet origin <SERVED>`
   first) exiting `0` means staging already serves `<P>`: report that and push
   nothing.
   Report the staging result as the deploy steps say; production stays untouched
   until `/promote`.

## After the push

```bash
rm -rf packages/web/.next
```

`/quickship` runs from the primary checkout, so there is no worktree to reclaim.

**Delete only `.next` — never `packages/*/dist`.** Same rule as `/ship`, and the
primary checkout is not an exception to it: a repo's checks may read a package's
build output, so a checkout stripped of `dist` cannot run its own next gate, and the
failure reads as a red gate rather than as a missing build — which is worse than
the disk it frees.
