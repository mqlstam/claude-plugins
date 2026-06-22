---
name: spinup
description: Bring up THIS git worktree's own private runnable stack (own ports, DB, Temporal) so the agent can run and self-verify the app in parallel with other worktrees. For repos with a scripts/worktree-env.sh (see .claude/rules/worktree-runtime.md).
disable-model-invocation: true
allowed-tools: Bash(source *), Bash(. *), Bash(docker *), Bash(pnpm *), Bash(PORT=*), Bash(./scripts/*), Bash(git *)
---

# Spin up this worktree's stack

Brings up a fully private, runnable stack for the current worktree so it can be
exercised + self-verified independently of other worktrees. Run this **only when
you actually need to run/test the app** — not for plan- or review-only sessions
(it starts ~8 containers + a dev server and uses real RAM).

Requires the repo's `scripts/worktree-env.sh` + parametrized compose ports (see
`.claude/rules/worktree-runtime.md`). If they're absent, this repo isn't set up
for per-worktree stacks — use `pnpm dev:up` on the canonical checkout instead.

## Steps (do in order; STOP and surface any failure — never retry blindly)

1. **Claim the port lease + point DB tooling at this worktree** (sourced — sets
   `COMPOSE_PROJECT_NAME=wt-<slug>`, claims a free port block, rewrites the host
   port inside `packages/db/.env.local`, exports the host BFF edges):
   ```bash
   source scripts/worktree-env.sh
   ```
2. **Bring up the private stack** (own postgres/temporal/minio/etc.; the DB init
   entrypoint fires on the fresh namespaced volume):
   ```bash
   docker compose up -d
   ```
3. **Migrate + seed THIS worktree's DB** (creates the deterministic dev login
   `dev@example.com` / `dev-password-2026!` in this worktree's own DB):
   ```bash
   pnpm db:setup:dev
   ```
4. **Start the host BFF on this worktree's port** (background; Fast Refresh):
   ```bash
   PORT="$WT_BFF_PORT" pnpm dev
   ```
5. **Report** the URLs for the self-verify loop:
   - App: `http://localhost:$WT_BFF_PORT`  (log in `dev@example.com` / `dev-password-2026!`)
   - Temporal UI: `http://localhost:$WT_TEMPORAL_UI_PORT`
   - psql: `docker exec -it ${COMPOSE_PROJECT_NAME}-postgres-1 psql -U endoxia -d endoxia_workspace`

Canonical `pnpm dev:up` is unaffected — it never sources `worktree-env.sh`, so
its ports/DB/project name stay the defaults. Tear down with `/teardown`.
