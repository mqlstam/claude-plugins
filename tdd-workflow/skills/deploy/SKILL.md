---
name: deploy
description: Deploy current main to production by pushing a deploy-* tag. Deploy is decoupled from merge — merging never deploys; this is the only thing that ships to prod.
disable-model-invocation: true
argument-hint: ""
allowed-tools: Bash(git *), Bash(gh *)
---

# Deploy

Ships the current tip of `main` to production. **Deploy is intentional, not
automatic:** the Deploy workflow triggers only on a `deploy-*` tag (or manual
dispatch), never on a push to `main`. So `/ship` and `/merge` land work on `main`
without touching prod — `/deploy` is the one command that ships it.

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
gh run watch "$RID" --exit-status \
  && echo "LIVE: https://app.endoxia.com  (deployed ${SHA:0:8})" \
  || echo "DEPLOY FAILED — prod stale. A Docker Hub token 404 / transient pull error is retryable: 'gh run rerun $RID --failed'."
```

Do all of the above in a single response; tool calls only. The deploy builds +
pushes images and SSHes to the prod box — it is the one place that changes
production. A transient registry pull error on the box is retryable with
`gh run rerun <id> --failed` (no code change).
