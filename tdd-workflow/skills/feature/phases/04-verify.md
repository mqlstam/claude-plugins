# Phase 4: VERIFY

## Goal

Two independent checks of the finished change, run in parallel with the project's
local validation:

- **Correctness review**, always: a fresh context reads the diff against the slice,
  read-only, preferably on another model family.
- **Runtime verification**, when the diff changes something a user or caller can
  observe: the change is used on the worktree's own stack, with evidence.

Agents run in the parent's checkout (no `isolation: "worktree"`; nested worktree
isolation is known broken, Claude Code issues #47548 #27881 #50850 #39886 #41010).

## Why this shape

Measured over 2026-09-01 .. 10-20 (196 specialist reports classified, 811 runs timed):

- **Defects came from an independent reader and from running the app.** The reviewer
  found a concrete behavioural defect in 31% of its runs (42% in October): races,
  isolation leaks, unmet requirements, deploys that would not work. All 7 defects the
  e2e specialist found came from driving the app in a browser, not from the spec it
  wrote.
- **The checklist lanes rarely did.** the Temporal checker came back clean 27 of 35
  times, and its domain already has static checks. the dead-code finder found nothing
  real in 10 of 10. In 21 of 70 reviews the most serious finding was style that lint
  already owns, and each such finding still cost a fix-and-rerun round.
- **Rounds, not waiting, made VERIFY slow.** Median 70 minutes in about 2 rounds. A
  parallel round lasts as long as its slowest agent, and every non-defect finding buys
  another round.

So VERIFY keeps the two parts that find defects and drops the per-concern specialists.
What those specialists knew (replay across a deploy, live equals reload, an old
implementation left behind, a test that is green for the wrong reason) lives on as
method in the two briefs. A new concern belongs in a brief as method, or in a
deterministic check. It does not get its own agent.

## Step 1 - prepare

**The review context.** Collect:

- the slice doc path and its acceptance criteria;
- the base, `git merge-base HEAD origin/main` (uncommitted work is part of the change);
- what you ran: the tests, the mutation record, what you verified yourself;
- decisions you took on purpose and why.

Pass facts, not your reasoning. The review's value is that it does not share your
view of the code.

**Whether runtime verification is in scope.** It is when the change alters anything
observable through a page, an API route, a job, a message or a mail. A change with no
observable effect (an internal refactor, docs, tooling) skips it; say so in one line.

When it is in scope, **start the stack first, in the background**, with the profile
that serves the touched paths. Follow the project's runtime rule for the command, the
stack cap and the profiles; fall back to `bash scripts/wt.sh docker compose up -d`.

## Step 2 - launch in one message

**a. Correctness review.**

- **The review runs on a model family other than the one that built the change.** A
  model reviewing its own family's output over-credits it (arxiv 2504.03846). Inside
  T3 Code (the `t3-code` MCP tools are available), call `orchestrator_capabilities`
  and pick an instance of the other family: a `codex` instance when Claude built the
  change, a `claudeAgent` instance when Codex did. Call `delegate_task` with
  `target: { providerInstanceId: <that instance>, model: <the first model it lists> }`,
  `mode: "async"`, `role: "review"`, `isolation: "shared"` (the reviewer must see
  uncommitted work), a `clientRequestId` unique to this round, and a title such as
  `VERIFY review <slice> r1`. The task text is the body of
  `${CLAUDE_PLUGIN_ROOT}/agents/correctness-reviewer.md` without its frontmatter,
  followed by the review context. Keep the returned `taskId`.
- **Otherwise**, or when the delegated task fails to start: the `correctness-reviewer`
  agent with `model: "opus"` and the review context, and say in the summary that the
  review ran on the builder's own family.

**b. Runtime verification**, when in scope: the `runtime-verifier` agent with
`model: "opus"`. Give it the slice, the base, the stack URL, the profile and the
services that profile leaves out. Launch it as soon as the stack answers. Do not hold
the review back for the stack.

**c. Local validation**, in the background, on the current tree. With a `ship:check`
script in this checkout's `package.json`, run `pnpm ship:check`. Add `--add-tests`,
`--add-package` or `--add-checks` only with `--reason`, for a boundary the automatic
selection misses. Never narrow it. Without `ship:check`, follow
`${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`, including its assertion-only sweep.
The reviewers do not write to the tree, so this run is valid unless a fix lands later.

Do not edit the tree while these run. Long commands use `run_in_background: true`.
Completions arrive as notifications, so never sleep-poll.

## Step 3 - judge the findings

You are the author. Read every finding against the code.

- **Real:** fix it. A defect found by running the app first gets a failing regression
  test at the boundary that owns the behaviour; then the fix.
- **Not real:** reject it with a one-line reason (the scenario cannot occur because
  ...). Keep the reason for the summary and for the next round.
- **Style or speculation:** do not act on it.

## Step 4 - re-check only what changed

- **After a non-trivial fix,** run one new round of the lane that found it. For the
  review, that is a new `delegate_task` call (new `clientRequestId`) or a new agent,
  carrying the original brief, the previous findings, your response to each, and the
  diff since the previous round. For runtime verification, re-check the affected
  criteria only.
- **A trivial fix** (one obvious line) needs no re-review.
- **Either way,** local validation runs again on the final tree.

## Step 5 - verdict

- **READY**: the review has no open blocker or major finding. Runtime verification is
  READY, or was skipped or NOT RUN with the reason stated. Local validation exited `0`
  on the final tree.
- **NEEDS_FIX**: a real finding is open, or validation failed (exit `1` names the step).
- **BLOCKED**: validation refused (exit `2`) for a reason you cannot fix here, or no
  review could run at all. A failed Codex task falls back to the agent; it is not
  BLOCKED by itself.

An interrupted command or any other nonzero exit is not a pass. NOT RUN is never a
pass: it always appears in the summary to the user.

## After the verdict

- **READY** -> write `.claude/.verify-state.json`:
  `{ "headSha": "<git rev-parse HEAD>", "timestamp": "<ISO 8601>", "verdict": "READY" }`.
  `/ship` checks it before merging main and committing; those integration steps
  do not re-arm VERIFY. Tell the user what was fixed, what you rejected and why, the
  runtime evidence, and anything NOT RUN. Suggest `/ship`.
- **NEEDS_FIX** -> fix in this chat, then step 4.
- **BLOCKED** -> surface it to the user. Do not ship.
