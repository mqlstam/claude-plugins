---
name: teardown
description: Surgically destroy THIS worktree's private stack and its volumes only. Hard-refuses the canonical project and any non-wt-* project (never wipes shared data).
disable-model-invocation: true
allowed-tools: Bash(source *), Bash(. *), Bash(./scripts/*)
---

# Teardown this worktree's stack

Runs the repo's hard-refusing teardown script. Removes only **this** worktree
project's containers + its label-scoped volumes; canonical `endoxia_*` data is
untouched. It refuses if `COMPOSE_PROJECT_NAME` is empty, `endoxia` (canonical),
or not a `wt-*` project — never `docker compose down -v`.

## Steps

```bash
source scripts/worktree-env.sh        # sets COMPOSE_PROJECT_NAME=wt-<slug> for this worktree
./scripts/teardown-worktree.sh
```

If the script refuses, it prints why (empty / canonical / non-`wt-*`). **Do not
override it** — that guard is the protection against wiping shared volumes.
