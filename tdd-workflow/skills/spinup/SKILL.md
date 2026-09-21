---
name: spinup
description: Bring up THIS git worktree's own private runnable stack (own ports, DB, Temporal, headless Chrome) so the agent can run and self-verify the app in parallel with other worktrees. Light by default; `--full` for the heavy sidecars. For repos with a scripts/worktree-env.sh (see .claude/rules/worktree-runtime.md).
disable-model-invocation: true
allowed-tools: Bash(source *), Bash(. *), Bash(docker *), Bash(pnpm *), Bash(PORT=*), Bash(./scripts/*), Bash(bash scripts/*), Bash(git *)
---

# Spin up this worktree's stack

Brings up a private, runnable stack for the current worktree so it can be
exercised + self-verified independently of other worktrees.

**Run this only when you actually need to RUN the app** — the start of a VERIFY
phase whose diff touches user-facing surface, or when the user asks. A stack's
lifetime is a NEED, not a session: plan-only, review-only and backend-only work
needs no containers, and a soft-stop follows when the run is done.

Requires the repo's `scripts/worktree-env.sh` + parametrized compose ports (see
`.claude/rules/worktree-runtime.md`). If they're absent, this repo isn't set up
for per-worktree stacks — use `pnpm dev:up` on the canonical checkout instead.

## Arguments

- _(none)_ — the **light** profile: `$WT_SPINUP_SERVICES`, today
  `core legal minio-init`, which compose expands through `depends_on` to
  postgres, valkey, temporal-db, temporal, core, legal, minio, minio-init.
  Sign-in, every page, a full chat turn and the Dutch legal connectors work.
  **Not** started: `search` (so no `brave_search` / `fetch_url`), the three
  Temporal workers (so no document processing, deep research or export), and
  `ml` (so `search_knowledge` cannot retrieve). The derivation and what each
  omission costs are in `scripts/worktree-env.sh` at the export.
- `--full` — everything a bare `docker compose up -d` creates. Its declared
  ceilings are ~25 GiB against a ~31 GiB Docker VM, so this is a
  one-stack-at-a-time profile. Use it when the slice is about uploads, parsing,
  deep research or export.
- `<service> ...` — an explicit root list, e.g. `/spinup core legal search ml`
  when the slice needs web fetch and knowledge retrieval but no workers.

## Steps (do in order; STOP and surface any failure — never retry blindly)

1. **Claim the port lease + point DB tooling at this worktree** (sourced — sets
   `COMPOSE_PROJECT_NAME=wt-<slug>`, claims a free 15-port block, rewrites the
   host port inside `packages/db/.env.local`, exports the host BFF edges, the
   worktree memory ceilings and `WT_CDP_PORT`):
   ```bash
   source scripts/worktree-env.sh
   ```
   Source it **bare**. A pipe (`source ... | tail`) runs it in a subshell and
   every export is silently discarded while the banner still prints.
2. **Check there is capacity for another stack.** Refuses with the running set
   and how to free a slot; the cap is derived from this machine's Docker VM
   memory divided by the profile's own declared ceilings.
   ```bash
   ./scripts/worktree-stack-cap.sh --check
   ```
   If it refuses, do **not** override it silently — tell the user which stacks
   are running and let them choose what to stop.
3. **Bring up the private stack** (own postgres/temporal/minio; the DB init
   entrypoint fires on the fresh namespaced volume). Light by default:
   ```bash
   docker compose up -d $WT_SPINUP_SERVICES     # light
   docker compose up -d                          # --full
   ```
   First spinup in a worktree BUILDS its own `endoxia-core:wt-<slug>` image;
   expect several minutes once.
4. **Migrate + seed THIS worktree's DB** (creates the deterministic dev login
   `dev@example.com` / `dev-password-2026!` in this worktree's own DB):
   ```bash
   pnpm db:setup:dev
   ```
   `core` will have crash-looped until this lands (`assertPricingExists` against
   an empty `model_pricing`). Read the logs, then restart it once:
   ```bash
   docker compose up -d core
   ```
5. **Start this worktree's headless Chrome** (own profile, own CDP port — so
   this session's browser tools never touch another worktree's cookies or
   pages):
   ```bash
   ./scripts/worktree-chrome.sh
   ```
6. **Start the host BFF on this worktree's port.** In a human terminal:
   ```bash
   PORT="$WT_BFF_PORT" pnpm dev
   ```
   From an agent tool call / hook / CI step, use the detached form instead — a
   non-interactive launcher reaps the process group and `pnpm dev` dies
   silently minutes later (`nohup … & disown` does not fix it):
   ```bash
   ./scripts/worktree-dev-detached.sh
   ```
7. **Report** the URLs for the self-verify loop:
   - App: `http://localhost:$WT_BFF_PORT` (log in `dev@example.com` / `dev-password-2026!`)
   - Temporal UI: `http://localhost:$WT_TEMPORAL_UI_PORT` (`--profile debug` only)
   - Chrome CDP: `http://127.0.0.1:$WT_CDP_PORT`
   - psql: `docker exec -it ${COMPOSE_PROJECT_NAME}-postgres-1 psql -U endoxia -d endoxia_workspace`
   - Say which profile is up and what it therefore cannot do.

Canonical `pnpm dev:up` is unaffected — it never sources `worktree-env.sh`, so
its ports, DB, project name and memory ceilings stay the defaults. When the run
is finished, soft-stop (`docker compose stop` + `./scripts/worktree-chrome.sh
--stop`) to free RAM while keeping the data, or `/teardown` to remove it.
