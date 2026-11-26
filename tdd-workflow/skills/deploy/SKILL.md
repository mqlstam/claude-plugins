---
name: deploy
description: Release current main to staging sooner than staging's own rhythm, or by hand - by joining a deploy run that already builds it, or by dispatching the deploy workflow with target=staging. It pushes no tag. Merging never deploys; production is reached only by promoting what staging serves (/promote). Runs when the user types /deploy, after /approve deploy (one run), or under the owner's standing approval (`release-approval-gate.py --status` names it; docs/STANDING-APPROVAL.md).
release-actions: dispatch-staging
argument-hint: ""
allowed-tools: Bash(git *), Bash(gh *), Bash(date *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py *)
---

# Deploy

Builds the current tip of `main` and releases it to STAGING, now. **Nobody needs
this after a merge:** staging follows main by itself (Endoxia: once an hour, when
main moved, `staging-rhythm.yml` dispatches the same staging deploy), and a
builder's work ends at its merge. `/deploy` is for when staging must have main
sooner, or when the rhythm left a head undeployed (a red or cancelled run, which
it never retries).

**The deploy starts on a dispatch, never on a tag.** The Deploy workflow runs only
when it is dispatched (`gh workflow run deploy.yml`), never on a push to `main`
and never on a pushed tag. **Never create or push a `deploy-*` tag:** the staging
release writes that tag itself once staging has verified the release, so a pushed
one starts nothing and falsely marks a commit as a release staging verified
(Endoxia #3342; the migration checks take the newest such tag as their base). The
release gate refuses the push.

A dispatch with `target=staging` stops at staging (`https://staging.endoxia.ai`);
production is a separate, manual promote of what staging serves (`/promote`, or
`gh workflow run promote.yml -f sha=<sha>`), with no rebuild. Since slice 806 a
staging deploy builds, scans, integrates and boots, and runs no unit lane: fast
local checks precede each merge and short main CI judges the merged range. The
whole suite gates the exact production SHA at promote; a verified green verdict
for that SHA may be reused.

**A deploy joins the train.** The workflow's concurrency group lets one run roll
and one wait, and a newer run replaces the waiting one. So `/deploy` first asks
whether a run in flight already builds a commit that contains `main`'s tip (the
rhythm's own run included); if one does, it waits on that run and dispatches
nothing. Otherwise it dispatches.

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
a piped release (a pipe reports its last command's status, so a failed release
read as success and used up the approval on 2026-10-07).

1. **The workflow must be enabled.** If the state above is not `active`:
   `gh workflow enable deploy.yml`.

2. **The commit.** `<SHA>` is the full 40-character SHA from "main @ origin".
   Make it local for step 3: `git fetch --quiet origin main`.

Before joining or dispatching, read the newest health status for this exact SHA:
   `gh api repos/:owner/:repo/commits/<SHA>/statuses --jq '[.[] | select(.context == "endoxia/main-health")][0] // null'`.
   The statuses API is newest first. `failure` or `error` means STOP: this commit
   cannot deploy to staging; name the failing status and its target URL. `pending`
   or no matching status means warn that health is unconfirmed, then continue.
   An API error is not a green status: report health as unknown and warn.

3. **A run that already builds it.**
   `gh run list --workflow deploy.yml --limit 20 --json databaseId,status,headSha,headBranch,createdAt,event --jq '.[] | select(.status != "completed")'`
   gh reports a finished run as `completed`; every other status (`queued`,
   `requested`, `waiting`, `pending`, `in_progress`) is in flight. The group holds
   at most one rolling and one waiting run, so they are among the newest; 20 also
   covers a burst of dispatches whose replaced runs are newer and already
   `completed`.

   Only a run whose `headBranch` is `main` is a candidate: a deploy of main goes
   to staging (alone, or first and then on to production), while a run on another
   branch is a hotfix that leaves staging untouched. The list does not carry a
   dispatch's `target`, so step 8 checks that a joined run released staging. For
   each candidate, oldest first, with `<RUN_SHA>` its `headSha`:
   `git merge-base --is-ancestor <SHA> <RUN_SHA>`
   - exit `0` → that run contains `<SHA>`: `<id>` is its `databaseId`. Dispatch
     nothing; go to step 6.
   - exit `1` → it does not; next candidate.
   - exit `128` → `<RUN_SHA>` is not local: `git fetch --quiet origin <RUN_SHA>`,
     then ask once more.

   No candidate contains it → step 4.

4. **Dispatch, to staging only, with nothing after it.**
   `gh workflow run deploy.yml --ref main -f target=staging`

   `--ref main` builds main's tip as GitHub resolves it then; when main moved
   since step 2 the run carries the newer head, which contains `<SHA>`.

   **The one input beside the target is `accept_off`.** Where the repository's
   staging release refuses a capability its manifest declares off unless the
   dispatch names it (Endoxia: `docker/environments/staging.capabilities.json`,
   `.claude/rules/ships-turned-on.md`), a dispatch that names nothing is refused
   in its first job. Read the list at `<SHA>`, first:
   `gh api -X GET repos/:owner/:repo/contents/docker/environments/staging.capabilities.json -f ref=<SHA> -H 'Accept: application/vnd.github.raw' --jq '[.capabilities | to_entries[] | select(.value.expected == "off") | .key] | join(",")'`
   A non-empty answer `<OFFS>` (a comma list without spaces) is added exactly as
   read: `gh workflow run deploy.yml --ref main -f target=staging -f accept_off=<OFFS>`,
   and the message of step 6 names them: this deploy leaves those capabilities off
   on staging, as its manifest declares. An empty answer or a 404 (no such
   manifest) adds nothing.

   Never any other input on your own: `accept_recreate`, `accept_drain`,
   `accept_disk_reading`, `waive_cve`, `carry` and `allow_replacing` are the
   owner's word for one release. A run that refuses for the lack of one prints the
   dispatch it needs; report that line and stop. And never another `target`:
   `both` and `production` are `/release`, `/promote` and `/hotfix`, and this
   skill's approval covers a dispatch that names `target=staging` and no other.

5. **The run id.** The dispatch prints the created run's URL,
   `https://github.com/<owner>/<repo>/actions/runs/<id>`; `<id>` is its last path
   segment. Only when the output carries no run URL:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py find deploy.yml <SHA>`
   prints `run_id=<id> url=<url>`. GitHub registers the run within seconds
   (3-22 s measured on 45 runs); the script stops at 60 s with
   `release_run_find_deadline` and the `gh run list` to read instead. It names the
   NEWEST run on that commit, which is an earlier one when this commit was deployed
   before and the new run is not registered yet: confirm with
   `gh run view <id> --json createdAt,event` that it is a `workflow_dispatch`
   created after the dispatch.

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
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py await <id> --deadline-min 268 --slice-min 9`
   Exit 6 (`release_run_still_running`) means the slice ended and the run goes
   on: say in one line "run `<id>` is `<status>`, still watching" and run the
   same command again. No `sleep`, no run watch, and no end of the turn between
   slices. When another deploy run is `in_progress` ahead of `<id>`
   (step 3 listed one, or `<id>` is not `in_progress` itself), `<id>` starts
   only when that run ends, and the run ahead is a dispatch whose target may be
   `both`: use `--deadline-min 729`.

   The deadline counts from the run's own start (GitHub's `run_started_at`), so
   every slice measures against the same bound. The 268 minutes: the longest
   chain of job `timeout-minutes` from the dispatch to the staging verdict is 253
   (deploy.yml: `changes` 10 → `build-and-push` 45 at its largest matrix entry,
   or `docx-sandbox-image` 45 → `release-manifest` 5 = 60, with `smoke` 20,
   `integration` 17, `integration-long` 30, `integration-quarantine` 6 and
   `migrations` 5 beside it from t=0; then release.yml for staging:
   `verify-images` 10 → `deploy` 163 → `verify` 10 → `notify` 10 = 193, `admit`
   skipped on staging, `notify` also waiting on `sourcemaps` 10 beside
   `verify`), plus 15 minutes for runners to pick the jobs up (a placeholder:
   GitHub does not bound queueing). Every run of deploy.yml shares
   its `deploy-staging` group, and release.yml holds `release-staging` for
   staging and `production-lane` for production at workflow level, so the run
   ahead holds this one for its whole chain: with `target=both` 461 (the 253,
   then release.yml for production: `verify-images` 10 → `admit` 15 → `deploy`
   163 → `verify` 10 → `notify` or `tag-production` 10 = 208; `gate` 49 runs
   beside the builds), giving 729 with this one's 268. A run ahead whose
   production section also queues in
   `production-lane` behind a `/promote` can pass even that bound; the
   NOT CONCLUDED line below covers it. Derived from Endoxia #1765 (slice 975);
   re-derive when a timeout or concurrency group in either workflow changes. A
   slice is 9 minutes because the Bash tool stops a foreground command at 10.
   At the run's bound the script exits 5 with `release_run_await_deadline NOT CONCLUDED`.
   Then the FIRST line of the message is: "NOT CONCLUDED: run `<id>` is `<status>`
   past its own 268-minute bound (729 behind another run)
   (`<url>`); it has no verdict and is not done."
   Never report it as succeeded, failed or finished, and never say "I'll report
   back". The run stays visible: `pnpm release:status` lists it under "Open release
   runs" until it concludes, so the next status check shows it; inside T3 Code the
   thread's last message names the run id as open. This is the only way the turn
   ends without a verdict.

8. **The verdict.** Confirm with a second read,
   `gh run view <id> --json status,conclusion,headSha`, and report:
   - `conclusion` `success` → "STAGING: https://staging.endoxia.ai serves `<sha8>`
     (run `<id>`); production unchanged, promote with /promote." For a JOINED run,
     first make sure it released staging:
     `gh run view <id> --json jobs --jq '[.jobs[] | select(.conclusion == "success") | .name]'`
     names its staging release job (Endoxia: the names that start with `Release
     to staging`). A run without one was a production-only dispatch and says
     nothing about staging: step 4.
   - `cancelled` → a newer run replaced this waiting one (or someone cancelled
     it). Step 3 again: a run in flight that contains `<SHA>` is the one to follow
     (steps 6-7 with its id; no dispatch). None, and this /deploy dispatched
     nothing yet → step 4. None after its own dispatch → report it cancelled.
   - anything else → "DEPLOY `<conclusion>`: staging still serves the previous
     release; production is untouched." Then the REASON, which the await printed
     under its `conclusion=` line (`release-run.py reason <id>` prints it again):
     - `reason=billing …` → report that line as it stands: GitHub refused to start
       the jobs, nothing was built, and it is the owner's action (Settings →
       Billing & plans). No rerun and no new dispatch until the owner says billing
       is fixed; then `gh run rerun <id> --failed`, which this approval covers once.
     - `reason=job-failure …` → name the failed job, its step and the first
       annotation. For more, `gh run view <id> --log-failed > /tmp/deploy-<id>.log 2>&1`
       and read the failing step from that file. A staging run whose failed job
       says `EC2 Spot interruption` is rerun by RunsOn: `await` reports that
       attempt red and does not wait for the rerun, so you read the run again
       after its next attempt (the same `await` with the same run id), and
       never dispatch a second one. A transient error (registry
       pull, token, runner) on a run this /deploy dispatched is retried with
       `gh run rerun <id> --failed`, which its approval covers once when the
       dispatch printed the run's URL; on a joined run this /deploy dispatched
       nothing, so step 4 (its own dispatch) is the retry. A code
       failure needs a fix and a new /deploy.

**Report the `conclusion`, never an exit code.** "It is live" may only follow
`status == completed` and `conclusion == success`, confirmed by the second read.
A run watch with `--exit-status` once exited 0 for run 35550388776 whose conclusion
was `failure`; the await reads the API for exactly that reason. The report names
the environment (staging), the SHA it serves and the run, or the failure's reason.
