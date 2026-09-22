---
name: deploy
description: Build current main and release it to staging by pushing a deploy-* tag. Merging never deploys; production is reached only by promoting what staging serves (/promote).
disable-model-invocation: true
argument-hint: ""
allowed-tools: Bash(git *), Bash(gh *)
---

# Deploy

Builds the current tip of `main` and releases it to STAGING. **Deploy is
intentional, not automatic:** the Deploy workflow triggers only on a `deploy-*`
tag (or manual dispatch), never on a push to `main`. Since Endoxia slice 603 a
tag stops at staging (`https://staging.endoxia.ai`); production is a separate,
manual promote of what staging serves (`/promote`, or `gh workflow run
promote.yml -f sha=<sha>`), with no rebuild.

## Context (pre-computed)

- main @ origin: !`git ls-remote origin refs/heads/main | cut -f1`
- Deploy workflow state: !`gh api repos/:owner/:repo/actions/workflows/deploy.yml --jq .state 2>/dev/null`
- Last deploy: !`gh run list --workflow=deploy.yml --limit 1 --json headSha,conclusion,createdAt -q '.[0]|"\(.createdAt[5:16]) \(.conclusion // "running") \(.headSha[0:8])"' 2>/dev/null`

## Steps (single message)

```bash
set -euo pipefail

# Deploy must be enabled (it can be disabled to save Actions cost on idle days).
state=$(gh api repos/:owner/:repo/actions/workflows/deploy.yml --jq .state 2>/dev/null || echo unknown)
if [ "$state" != active ]; then
  echo "Deploy workflow is '$state' — enabling it for this deploy."
  gh workflow enable deploy.yml
fi

SHA=$(git ls-remote origin refs/heads/main | cut -f1)
echo "Deploying main @ ${SHA:0:8} to production."

# If CI is active and ran on this SHA, refuse a non-green one. If CI is disabled
# (pre-beta cost-saving), there's no check to gate on — local /ship was the gate.
ci=$(gh api repos/:owner/:repo/actions/workflows/ci.yml --jq .state 2>/dev/null || echo unknown)
if [ "$ci" = active ]; then
  concl=$(gh run list --workflow=ci.yml --branch main --limit 20 --json headSha,conclusion \
    -q "[.[]|select(.headSha==\"$SHA\")][0]|.conclusion" 2>/dev/null || true)
  [ "$concl" = success ] || { echo "REFUSE: main@${SHA:0:8} CI=${concl:-none}. Merge/CI must be green first."; exit 1; }
fi

TAG="deploy-$(date -u +%Y%m%d-%H%M%S)"
git tag "$TAG" "$SHA" && git push origin "$TAG"

# Match the deploy run by SHA (avoid the --limit-1 race), then watch it to green.
RID=""
for _ in $(seq 1 20); do
  RID=$(gh run list --workflow=deploy.yml --limit 10 --json databaseId,headSha \
    -q "[.[]|select(.headSha==\"$SHA\")][0]|.databaseId" 2>/dev/null || true)
  [ -n "$RID" ] && break; sleep 3
done
[ -n "$RID" ] || { echo "Deploy run for ${SHA:0:8} not registered yet — check 'gh run list --workflow=deploy.yml'."; exit 1; }
# Watch for the live display only. `gh run watch --exit-status` is NOT a
# trustworthy verdict: on 2026-09-21 it exited 0 for run 35550388776 whose
# conclusion was `failure`, so a deploy that never rolled would have been
# reported as LIVE while prod sat on the previous release. Read the verdict
# from the API instead, and never branch on the watch's exit code.
gh run watch "$RID" || true

# The watch can also return before the run is terminal, so settle on `status`
# before reading `conclusion` — an in-flight run has conclusion "".
concl=unknown
for _ in $(seq 1 60); do
  st=$(gh run view "$RID" --json status -q .status 2>/dev/null || echo unknown)
  if [ "$st" = completed ]; then
    concl=$(gh run view "$RID" --json conclusion -q .conclusion 2>/dev/null || echo unknown)
    break
  fi
  sleep 10
done

if [ "$concl" = success ]; then
  # Since Endoxia slice 603 a deploy-* tag releases to STAGING only; production
  # is reached by promoting (/promote, or: gh workflow run promote.yml -f sha=...).
  echo "STAGING: https://staging.endoxia.ai  (released ${SHA:0:8}; production unchanged, promote with /promote)"
else
  echo "DEPLOY ${concl}: staging still serves the previous release; production is untouched."
  echo "  Why:    gh run view $RID --log-failed"
  echo "  Retry:  gh run rerun $RID --failed   (transient registry/token errors only)"
  exit 1
fi
```

**Report the `conclusion`, never the watch's exit code.** If you summarise this
deploy to the user, the sentence "it is live" may only follow `conclusion ==
success` — confirmed by a second read, not inferred from a command that
returned. The same caution applies to any `| tail` you add around a gate: a pipe
discards the real exit status and reports the pipe's, which has produced three
separate false greens in one session (`gh run watch`, `check:contracts` with a
failed link, and a `turbo` lane with a failed task). Redirect to a file and echo
`$?` instead.

Do all of the above in a single response; tool calls only. The deploy builds +
pushes images and SSHes to the prod box — it is the one place that changes
production. A transient registry pull error on the box is retryable with
`gh run rerun <id> --failed` (no code change).
