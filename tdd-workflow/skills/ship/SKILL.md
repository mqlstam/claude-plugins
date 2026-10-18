---
name: ship
description: Commit, gate the change, push, open the PR and land it on main. Runs when the user types /ship, or when Claude invokes it after the user typed /approve ship (one run).
release-actions: merge
argument-hint: "[PR title]"
allowed-tools: Bash(git *), Bash(gh *), Bash(npm run *), Bash(pnpm *), Bash(node *), Bash(jq *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/wait-mergeable.py *)
---

# Ship

## Context (pre-computed)

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff stats: !`git diff --stat HEAD`
- Recent commits on this branch: !`git log --oneline -5`
- Land queue: !`jq -r 'if (.scripts // {} | has("ship:gate") and has("ship:land")) then "yes" else "no" end' package.json 2>/dev/null || echo no`

Every command below is ONE plain command with its values written out: no
variables, no `$(...)`, git as its own command, no pipe on a release command (the
release gate refuses a piped release: a pipe reports its last command's status).
Anything that runs longer than a few seconds runs with `run_in_background: true`;
act on its completion notification, never `sleep` or poll.

## Both flows start here

1. **Something to ship.** Status empty and `git log --oneline origin/main..HEAD`
   empty → STOP: nothing to ship.
2. **VERIFY ran on this diff.** `jq -r .headSha .claude/.verify-state.json` must
   print what `git rev-parse HEAD` prints. A missing file or another SHA → STOP:
   "VERIFY has not run on this diff. Complete it (re-enter /feature or /refactor)
   before /ship."

**Land queue `no`:** read `${CLAUDE_SKILL_DIR}/generic.md` and follow it, then
"After it lands" below. **Land queue `yes`:** the steps below. The queue is the
only way onto `main` here: nothing merges a PR or pushes `main` around it.

## Land queue: the scripts own the mechanics, you own the judgment

`pnpm ship:gate` picks what to run from the change, reuses the record of a tree
already proven (VERIFY's run, usually), and runs beside other worktrees' gates.
`pnpm ship:land` hands the PR to one integrator that tests the merged result once
and squash-merges in queue order. Neither needs main watched, merged in or
re-gated because it moved. Yours: the commit message, what the floor cannot see,
the PR text, and reading the outcome.

3. **Commit.** Stage what belongs to the change (`git add <paths>`; never secrets or
   scratch files) and commit in conventional form, `type(scope): what changes for
   the reader` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, ...;
   `commit-msg` refuses anything else). `$ARGUMENTS`, when given, is the subject.
   Then `git status --short` must print nothing: the gate records the working tree,
   and `pre-push` looks up the tree of the commit it pushes, so a file left
   uncommitted or untracked makes the two differ.

4. **Gate.** Read the diff (`git diff --stat origin/main...HEAD`, then the hunks
   that matter). The floor already selects lint and typecheck of what is affected,
   Prettier on the changed files, the checks whose inputs changed, every test that
   imports a changed file, every changed test and the registered always-run tests.
   Add what no import reaches:
   - a contract crossed over HTTP, SSE, a queue or the database rather than an
     import (a Core route the BFF calls, an event shape) → the other side's tests:
     `--add-package <@scope/pkg>` or `--add-tests <path|glob>`;
   - text loaded at runtime (a prompt, a skill, a SQL migration, a JSON/YAML
     config) → the tests that load it, such as its budget or eval test;
   - a check whose subject you changed without touching its declared inputs →
     `--add-checks <check:a,check:b>`;
   - a change whose reach you cannot bound (build tooling, the runtime, a helper
     everything calls) → `--full`.

   Any addition needs `--reason "<one line: why the floor misses each>"`, given
   once: the gate keeps one reason per run, and a second `--reason` replaces the
   first. You only add; nothing narrows the floor. With `run_in_background: true`
   and the Bash `timeout` at `7200000` (a full gate may hold for 75 minutes, past
   the 30-minute default): `pnpm ship:gate --label ship` plus your additions. It
   prints what it ran and why. Write the PR title and body while it runs.
   - `0` → passed (or reused a record of this tree): step 5.
   - `1` → it names what failed and where the full output is. Fix it here,
     commit the fix, run step 4 again.
   - `2` → refused: the environment, not the code (wrong Node major, a missing
     build). Do what its message says and run it again; never work around it.
   - `75` → no slot came free before its deadline: run it again once; a second
     `75` → report it and STOP.

5. **Push:** `git push -u origin <branch>`, never `--no-verify`. `pre-push` reuses
   the gate's record for this commit's tree; if it runs the gate itself, the tree
   or the `origin/main` it is judged against changed after step 4: read why before
   going on. If it refuses (no record covers the commit and the checkout is not
   that commit), do what it says.

6. **The PR.** When `gh pr view --json number,state` answers `OPEN` (a second pass
   after a conflict), that is `<PR>`. Otherwise
   `gh pr create --title "<title>" --body "<body>"`: the title is the commit
   subject; the body says what changed and why, the test plan, and each gate
   addition with its reason. `<PR>` is the number it prints.

7. **Land**, with `run_in_background: true` and the Bash `timeout` at its ceiling,
   `7200000`:
   `pnpm ship:land land --pr <PR> --max-wait-ms 7000000`
   then end your turn and act on the notification. The queue derives its own
   deadline from what is ahead of it; `--max-wait-ms` keeps it inside the
   background ceiling (7,200,000 ms less 200 s for the exit to be read). A run the
   harness stops anyway is safe, and like `75` it used the approval: `/merge <PR>`
   re-attaches to the same entry, and an integrator that died is taken over.

8. **The outcome is its exit code.** Its last lines say what happened.
   - `0` landed: it prints main's new SHA. Report "Landed #<PR> on main as
     <sha8>.", then "After it lands".
   - `3` textual conflict: it names the files and the change it collided with.
     `git fetch origin main`, then `git merge --no-edit origin/main`, resolve
     keeping both sides' intent (a slice index both sides appended to: keep both
     lines), commit, and go back to step 4. When `origin/main` does not hold the
     colliding change yet, it is still ahead of you in the queue
     (`pnpm ship:land status`): land again once it has landed.
   - `1` the gate failed on the merged result and named this PR: the break is
     between this change and what landed before it. Merge `origin/main` as for
     `3`, then step 4 reproduces it on the merged tree; fix, commit, step 4.
   - `4` refused. Read the reason it printed: a newer push of this PR superseded
     the entry, or a batch was already landing the old head → run step 7 again
     (after that batch, for the second); the PR was closed, retargeted, or GitHub
     refused the merge → report the reason and STOP.
   - `6` main is red on its own: what failed on the merged result also fails on
     `origin/main` alone, so no PR was blamed and none of the batch merged. Report
     the evidence it printed (the failing steps and tests on main) and STOP: land
     again once main is green. Fixing main is its own change, through the queue.
   - `2` the land could not decide (it says which: an environment problem, a
     merge GitHub accepted but did not report merged in time, or a bisect whose
     reds disagreed) → run step 7 once more; it re-attaches to the same entry. A
     second `2` → report it and STOP. It promises nothing: never report the PR
     as not merged (`pnpm ship:land status` shows where it is).
   - `5` merged, but main is not, or may not be, the tree the queue tested: main
     changed outside the queue, or the batch's merges stopped part-way and main
     sits at a prefix it gated only afterwards (it prints that gate's verdict).
     STOP and tell the owner, quoting what it printed; what happens to main is the
     owner's call.
   - `75` the queue ahead outlasted the deadline: report that #<PR> is still
     queued (`pnpm ship:land status`) and that `/merge <PR>` waits for it again.
   - anything else: report its output and STOP.

   A land that exited `1`, `3`, `4` or `6` landed nothing, so it did not use this
   /ship's approval: steps 4-8 again are this same run. Every other exit used it.

## After it lands

0. **Land queue only: delete the remote branch.** `git push origin --delete <branch>`.
   A "remote ref does not exist" means the repository already deleted it; that is
   fine. (The generic flow deletes it in its merge step.)

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

**Landing does NOT deploy.** A deploy fires only on a `deploy-*` tag (or a manual
dispatch), never on a push to `main`. `/ship` puts the work on `main`; `/deploy`
releases it, to STAGING where the project has one (Endoxia, slice 603), and
production is a separate `/promote`.
