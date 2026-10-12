---
name: deploy
description: Build current main and release it to staging by pushing a deploy-* tag. Merging never deploys; production is reached only by promoting what staging serves (/promote). Runs when the user types /deploy, or after /approve deploy (one run).
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
promote.yml -f sha=<sha>`), with no rebuild.

**This skill starts the run and hands the session back.** It does not watch the
run in the foreground: that held the session for the whole ~22-minute run, 93
minutes of session time a day. The verdict arrives later, from a background wait
that reads the run's `conclusion`.

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

3. **CI, if it runs.** `gh api repos/:owner/:repo/actions/workflows/ci.yml --jq .state`.
   Only when that says `active`:
   `gh run list --workflow=ci.yml --branch main --commit <SHA> --json conclusion --jq '.[0].conclusion'`.
   Anything but `success` → STOP: "main@<sha8> CI=<value>; merge/CI must be green
   first." When CI is disabled (pre-beta cost saving) the local /ship gate was the
   gate.

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
   `release_run_find_deadline` and the `gh run list` to read instead.

6. **Hand back.** Tell the user in one or two lines: the deploy of `<sha8>` is
   running as run `<id>` (`<url>`); staging serves it when the run succeeds, in
   about 22 minutes; production is untouched until `/promote`.

7. **The verdict, in the background.** Start, with `run_in_background: true`:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release-run.py await <id> --deadline-min 190`
   and end your turn. Do not poll it and do not `sleep`: the completion
   notification wakes you.

   The 190 minutes are the run's own bound: the longest chain of job
   `timeout-minutes` from the tag to staging is 175 (deploy.yml: `changes` 10 →
   `build-and-push` or `docx-sandbox-image` 45 → `release-manifest` 5; then
   release.yml: `verify-images` 10 → `deploy` 85 → `verify` 10 → `notify` 10), plus
   15 minutes for runners to pick the jobs up (a placeholder: GitHub does not bound
   queueing). Re-derive when a timeout in either workflow changes. At the bound the
   script prints `release_run_await_deadline` with the run's status: say the run is
   still going and give its URL; that is not a failure.

8. **When the notification arrives.** Confirm with a second read,
   `gh run view <id> --json status,conclusion`, and report:
   - `conclusion` `success` → "STAGING: https://staging.endoxia.ai serves `<sha8>`;
     production unchanged, promote with /promote."
   - anything else → "DEPLOY `<conclusion>`: staging still serves the previous
     release; production is untouched." Then the reason:
     `gh run view <id> --log-failed > /tmp/deploy-<id>.log 2>&1`, and read the
     failing step from that file. A transient error (registry pull, token,
     runner) is retried with `gh run rerun <id> --failed`, which this deploy's
     approval covers once; a code failure needs a fix and a new /deploy.

**Report the `conclusion`, never an exit code.** "It is live" may only follow
`status == completed` and `conclusion == success`, confirmed by the second read.
A run watch with `--exit-status` once exited 0 for run 35550388776 whose conclusion
was `failure`; the background wait reads the API for exactly that reason.
