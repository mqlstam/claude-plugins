---
name: deploy
description: Release current main to staging, by joining a deploy run that already builds it or by pushing a deploy-* tag. Merging never deploys; production is reached only by promoting what staging serves (/promote). Runs when the user types /deploy, or after /approve deploy (one run).
release-actions: deploy-tag
argument-hint: ""
allowed-tools: Bash(git *), Bash(gh *), Bash(date *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py *)
---

# Deploy

Builds the current tip of `main` and releases it to STAGING. **Deploy is
intentional, not automatic:** the Deploy workflow triggers only on a `deploy-*`
tag (or manual dispatch), never on a push to `main`. Since Endoxia slice 603 a
tag stops at staging (`https://staging.endoxia.ai`); production is a separate,
manual promote of what staging serves (`/promote`, or `gh workflow run
promote.yml -f sha=<sha>`), with no rebuild. Since slice 806 a staging deploy
builds, scans, integrates and boots, and runs no unit lane: fast local checks precede each merge and short main CI judges the merged range.
The whole suite gates the exact production SHA at promote; a verified green
verdict for that SHA may be reused.

**A deploy joins the train.** The workflow's concurrency group lets one run roll
and one wait, and a newer tag replaces the waiting one. So `/deploy` first asks
whether a run in flight already builds a commit that contains `main`'s tip; if
one does, it waits on that run and pushes nothing. Otherwise it tags.

**This skill starts (or joins) the run and stays with it until it concludes**
(owner, 2026-10-20). The turn does not end, and nothing like "I'll report back"
is said, before the run's `conclusion` is read and reported: run 39493655874
was refused by GitHub for billing 7 s after its dispatch, 2 s after the thread
that started it had settled, and nobody noticed (the third billing refusal in
8 days). The wait is `release-run.py await`, in foreground slices that each fit
one Bash call; it reads the API's `conclusion`, never a watch's exit code.

## Context (pre-computed)

- main @ origin: !`git ls-remote origin refs/heads/main`
- Deploy workflow state: !`gh api repos/:owner/:repo/actions/workflows/deploy.yml --jq .state`
- Last deploy: !`gh run list --workflow=deploy.yml --limit 1 --json headSha,conclusion,createdAt`

## Steps

Each step is ONE plain command with the values written out literally: no
variables, no `$(...)`, no loops, no pipe on a release command. In a worktree
session Claude Code refuses commands it cannot read, and the release gate refuses
a piped release (a pipe reports its last command's status, so a failed push read
as success and used up the approval on 2026-10-07).

1. **The workflow must be enabled.** If the state above is not `active`:
   `gh workflow enable deploy.yml`.

2. **The commit.** `<SHA>` is the full 40-character SHA from "main @ origin".
   Make it local for step 3: `git fetch --quiet origin main`.

Before joining or tagging, read the newest health status for this exact SHA:
   `gh api repos/:owner/:repo/commits/<SHA>/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
   The statuses API is newest first. `failure` or `error` means STOP: this commit
   cannot deploy to staging; name the failing status and its target URL. `pending`
   or no matching status means warn that health is unconfirmed, then continue.
   An API error is not a green status: report health as unknown and warn.

3. **A run that already builds it.**
   `gh run list --workflow deploy.yml --limit 20 --json databaseId,status,headSha,createdAt,event --jq '.[] | select(.status != "completed")'`
   gh reports a finished run as `completed`; every other status (`queued`,
   `requested`, `waiting`, `pending`, `in_progress`) is in flight. The group holds
   at most one rolling and one waiting run, so they are among the newest; 20 also
   covers a burst of tags whose replaced runs are newer and already `completed`.

   Only a run whose `event` is `push` is a candidate: a tag always releases to
   staging, while a dispatch's target (staging, both, production) is not in this
   list. For each candidate, oldest first, with `<RUN_SHA>` its `headSha`:
   `git merge-base --is-ancestor <SHA> <RUN_SHA>`
   - exit `0` → that run contains `<SHA>`: `<id>` is its `databaseId`. Push
     nothing; go to step 6.
   - exit `1` → it does not; next candidate.
   - exit `128` → `<RUN_SHA>` is not local: `git fetch --quiet origin <RUN_SHA>`,
     then ask once more.

   No candidate contains it → step 4.

4. **Tag and push, by the tag's literal name.**
   - `date -u +%Y%m%d-%H%M%S` → `<TS>`
   - `git tag deploy-<TS> <SHA>`
   - `git push origin deploy-<TS>`

   The literal name matters twice: the release gate reads a ref pushed through a
   variable as the current branch, and it is the tag NAME that lets a later
   `gh run rerun <id> --failed` of this run go through under this same approval
   (once) instead of asking the user again.

5. **Find the run (foreground, seconds).**
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py find deploy.yml <SHA>`
   prints `run_id=<id> url=<url>`. GitHub registers the run 3-22 s after the push
   (measured, 45 tag pushes); the script stops at 60 s with
   `release_run_find_deadline` and the `gh run list` to read instead. It names the
   NEWEST run on that commit, which is an earlier one when this commit was deployed
   before and the new run is not registered yet: when "Last deploy" shows `<SHA>`,
   confirm with `gh run view <id> --json createdAt,event` that it was created after
   the push.

6. **Say what is running.** In one or two lines: the deploy of `<sha8>` runs as
   run `<id>` (`<url>`), or, when joined, rides on run `<id>`, which builds
   `<run8>` and contains it; it is being watched to its end in this turn;
   production is untouched until `/promote`.

   **Inside T3 Code** (the `mcp__t3-code__*` tools are present), the thread must
   not hand back as finished while a release run it started is open: every
   message until the verdict states the run id and that it is being watched,
   and the turn continues with step 7.

7. **The verdict, in this turn.** Run in the foreground, with the Bash tool's
   `timeout` at 600000 (its maximum):
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py await <id> --deadline-min 190 --slice-min 9`
   Exit 6 (`release_run_still_running`) means the slice ended and the run goes
   on: say in one line "run `<id>` is `<status>`, still watching" and run the
   same command again. No `sleep`, no run watch, and no end of the turn between
   slices. When another deploy run is `in_progress` ahead of `<id>`
   (step 3 listed one, or `<id>` is not `in_progress` itself), use
   `--deadline-min 365` instead: `<id>` starts only when that run ends.

   The deadline counts from the run's own start (GitHub's `run_started_at`), so
   every slice measures against the same bound. The 190 minutes: the longest
   chain of job `timeout-minutes` from the tag to staging is 175 (deploy.yml:
   `changes` 10 → `build-and-push` or `docx-sandbox-image` 45 →
   `release-manifest` 5; then release.yml: `verify-images` 10 → `deploy` 85 →
   `verify` 10 → `notify` 10), plus 15 minutes for runners to pick the jobs up (a
   placeholder: GitHub does not bound queueing). 365 is the run ahead's 175 plus
   this one's 190. Re-derive when a timeout in either workflow changes. A slice
   is 9 minutes because the Bash tool stops a foreground command at 10.
   At the run's bound the script exits 5 with `release_run_await_deadline NOT CONCLUDED`.
   Then the FIRST line of the message is: "NOT CONCLUDED: run `<id>` is `<status>`
   past its own 190-minute bound (365 behind another run) (`<url>`); it has no
   verdict and is not done."
   Never report it as succeeded, failed or finished, and never say "I'll report
   back". The run stays visible: `pnpm release:status` lists it under "Open release
   runs" until it concludes, so the next status check shows it; inside T3 Code the
   thread's last message names the run id as open. This is the only way the turn
   ends without a verdict.

8. **The verdict.** Confirm with a second read,
   `gh run view <id> --json status,conclusion,headSha`, and report:
   - `conclusion` `success` → "STAGING: https://staging.endoxia.ai serves `<sha8>`
     (run `<id>`); production unchanged, promote with /promote."
   - `cancelled` → a newer tag replaced this waiting run (or someone cancelled
     it). Step 3 again: a run in flight that contains `<SHA>` is the one to follow
     (steps 6-7 with its id; no tag). None, and this /deploy pushed no tag yet →
     step 4. None after a tag → report it cancelled.
   - anything else → "DEPLOY `<conclusion>`: staging still serves the previous
     release; production is untouched." Then the REASON, which the await printed
     under its `conclusion=` line (`release-run.py reason <id>` prints it again):
     - `reason=billing …` → report that line as it stands: GitHub refused to start
       the jobs, nothing was built, and it is the owner's action (Settings →
       Billing & plans). No rerun and no new tag until the owner says billing is
       fixed; then `gh run rerun <id> --failed`, which this approval covers once.
     - `reason=job-failure …` → name the failed job, its step and the first
       annotation. For more, `gh run view <id> --log-failed > /tmp/deploy-<id>.log 2>&1`
       and read the failing step from that file. A transient error (registry
       pull, token, runner) on a run this /deploy tagged is retried with
       `gh run rerun <id> --failed`, which its approval covers once; on a joined
       run this /deploy pushed nothing, so step 4 (a new tag) is the retry. A code
       failure needs a fix and a new /deploy.

**Report the `conclusion`, never an exit code.** "It is live" may only follow
`status == completed` and `conclusion == success`, confirmed by the second read.
A run watch with `--exit-status` once exited 0 for run 35550388776 whose conclusion
was `failure`; the await reads the API for exactly that reason. The report names
the environment (staging), the SHA it serves and the run, or the failure's reason.
