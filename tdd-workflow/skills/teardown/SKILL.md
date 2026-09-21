---
name: teardown
description: Surgically destroy THIS worktree's private stack, its volumes, its images and its Chrome profile only. Hard-refuses the canonical project and any non-wt-* project (never wipes shared data).
disable-model-invocation: true
allowed-tools: Bash(source *), Bash(. *), Bash(./scripts/*), Bash(bash scripts/*)
---

# Teardown this worktree's stack

Runs the repo's hard-refusing teardown script. Removes only **this** worktree
project's containers, its label-scoped volumes, its `wt-*` image tags and the
headless Chrome profile it left under `~/.cache/cdp/<slug>`; canonical
`endoxia_*` data is untouched. It refuses if `COMPOSE_PROJECT_NAME` is empty,
`endoxia` (canonical), or not a `wt-*` project — never `docker compose down -v`.

## Steps

```bash
source scripts/worktree-env.sh        # sets COMPOSE_PROJECT_NAME=wt-<slug> for this worktree
./scripts/teardown-worktree.sh
```

If the script refuses, it prints why (empty / canonical / non-`wt-*`). **Do not
override it** — that guard is the protection against wiping shared volumes.

## Teardown vs soft-stop — pick by what you mean

| You mean                                        | Do this                                                                  | Costs        |
| ----------------------------------------------- | ------------------------------------------------------------------------ | ------------ |
| "I'm done running for now, keep my seeded data" | `docker compose stop` + `./scripts/worktree-chrome.sh --stop`            | nothing      |
| "I'm done with this worktree"                   | `./scripts/teardown-worktree.sh`                                         | the data     |

The soft-stop form is what frees a slot for another worktree without losing a
seeded database — check `./scripts/worktree-stack-cap.sh --status` to see what
is holding the machine's capacity before deciding which stack to stop.
