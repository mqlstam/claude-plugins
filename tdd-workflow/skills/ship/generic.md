# Generic local validation

Use only when this checkout has no `ship:check` script. Read its own `package.json`;
never infer the commands from a remote branch. For shipping, integrate as /ship
step 4 does, `verify-state.py adopt-squash` and then
`git merge --no-edit -m "chore: integrate origin/main before shipping" origin/main`,
then validate the merged tree. The explicit subject satisfies conventional commit
hooks; keep those hooks enabled.
For VERIFY, run beside the read-only review, and again on the tree after any fix.

## How to run the validation (read this before step 3)

Measured over 47 ships: median invoke→merge **36 min**, p90 **140 min**, and **46%
of tool wall-clock was `sleep`/poll loops** around a validation that exceeds the 10-minute
`Bash` ceiling and gets backgrounded. Three rules, all non-negotiable:

1. **Split the validation into calls that FIT under the ceiling.** Never one
   `lint && typecheck && test && build && every check` mega-command: one call per
   command, so a failure names itself and a re-run repeats only what failed.
2. **Anything that might exceed ~8 minutes runs with `run_in_background: true`, and
   you WAIT FOR THE COMPLETION NOTIFICATION.** Do not write a `sleep 60`/poll loop.
   Do not re-run a command "to see where it is". While a background call runs, do
   something useful in the foreground (read the diff, draft the PR body) or simply
   wait. The plugin's `bash-shape-guard` hook refuses a foreground `sleep` above
   10 s, a `sleep` inside a loop, and a `| tail` whose status is then used, in
   subagents too.
3. **Independent steps run side by side.** Lint, typecheck and the format check read
   only the source and none waits on another: issue them in ONE message. Test and
   build start once those are green, in the background; step 5 runs after the
   build, because a check may read the build's output.

## Validation

3. **Find the repo's own commands.** `jq .scripts package.json` lists them with what
   each runs (step 5 reads both). The validation is the ones the repo has of `lint`,
   `typecheck`, `format:check`, `test` and `build`, each its own plain command
   through the repo's package
   manager: `pnpm <script>`, or `npm run <script>` where the repo has a
   `package-lock.json`. Print the ones it lacks: a command the repo does not have is
   skipped out loud, never silently.

4. **Run them.** This validation is the merge gate of a project with no selecting
   check: nothing here selects what a change reaches, so the whole suite runs. It is
   not the build loop, which runs only the test files it touched (tdd-methodology,
   "Running tests").
   - `pnpm lint`, `pnpm typecheck` and `pnpm format:check`: foreground, in one
     message. If a commit hook changed formatting, this checks the resulting tree.
   - `pnpm test`, then `pnpm build`: each with `run_in_background: true`, then wait
     for the notification.

   STOP on failure. A task runner that replays a cached pass (Turborepo's
   `FULL TURBO`) re-proves nothing after a failure: run it again with its cache off
   (`--force`).

5. **Project invariants: the assertions, never the writers.**
   - **The repo has its own sweep** (`gate:checks` in that list) → `pnpm gate:checks`
     with `run_in_background: true`, and nothing else for this step. The repo knows
     what a name cannot say: which of its scripts write, which run others, and which
     need a build first (Endoxia's skips `env:sync`, runs the links of
     `check:contracts` instead of the aggregate, and refuses a missing build).
   - **Otherwise** every other script in that list whose name starts with `check:`
     or `env:`, each as its own plain command (`pnpm check:markers`,
     `pnpm env:drift`, ...), except these, each printed with its reason:
     - **a writer**: a name with a part that is `update`, `sync`, `fix` or `write`
       (parts split at `:`, `-` and `_`: `check:budget:update`, `env:sync`,
       `check:fix-imports`), or a command that passes `--write`, `--fix` or an
       `--update…` flag, or sets an `UPDATE_…` variable. A writer rewrites a
       committed file to match the tree: it cannot fail, so running it reports
       green while redefining the target, and the commit then carries what it
       rewrote (Endoxia's `env:sync` once deleted 22 keys from `.env.example` on a
       /ship).
     - **an aggregate**: a script whose command runs another script of this set
       (`pnpm check:a && pnpm check:b`, `npm run check:x`, `run-s`, `npm-run-all`).
       Its members run on their own, so it would only run them twice.

   Why the name and the command, not a list: they are where a repository says what
   a script does, and a list in this plugin would be one repository's names. An
   aggregate that hides its members inside a script file is visible to neither; it
   costs time, never a wrong verdict. An assertion the rule wrongly drops is in the
   printed list, never silent.

   If an assertion legitimately needs a new baseline, update it DELIBERATELY as part
   of the change, ideally by hand-editing only the entries your diff moved, and say
   so in the PR. Never as a side effect of the pre-ship sweep.

Treat any failure as blocking.
