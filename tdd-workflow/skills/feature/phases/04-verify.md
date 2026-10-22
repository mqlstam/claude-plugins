# Phase 4: VERIFY

## Goal

Two independent checks of the finished change, run in parallel with the project's
local validation:

- **Correctness review**, always: a fresh context reads the diff against the slice,
  read-only, preferably on another model family.
- **Runtime verification**, when the diff changes something a user or caller can
  observe: the change is used on the worktree's own stack, with a recording of every
  user flow. The owner approves the change by watching that recording on the PR
  before it merges (owner, 2026-10-22), so it is required, not decoration.

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
observable through a page, an API route, a job, a message, a mail, or what a model
answers a user. A change with no observable effect (an internal refactor that moves no
request path, persisted format or rendered surface; docs; tooling; a test helper) is
out of scope: the manifest says so with its reason (`observable: false`,
`outOfScope`), and the PR shows that line.

Why this is spelled out: of the 15 user-visible PRs among the 40 merged before
2026-10-22, 10 carried no recording. Evidence was optional: READY allowed runtime
verification "skipped" on the author's own call, the verifier recorded only "when the
criterion is a flow rather than a state", and no manifest was written for a change
the author had ruled out. So the author ruled it out without a verifier ever running:
a provider cut "cannot be provoked locally" (after the same session had provoked it 4
of 4 times), a chat change covered "by tests with a stubbed fetch", a removed feature
recorded only as suite results. Hence:

- **Hard to provoke, or covered by tests, is not out of scope.** The change is
  observable; the verifier tries, and what it cannot do is NOT RUN with its reason.
- **A removal is observable:** record the surface without the removed thing.
- **Name the user flows** from the slice before the verifier starts: each path a user
  goes through to see the change, as the slice words it. Each one gets a recording, or
  a NOT RUN reason; the manifest and `verify-state.py evidence` hold VERIFY to it.

When it is in scope, **start the stack first, in the background**, with the profile
that serves the touched paths: `/spinup` (`--full` for the heavy sidecars), or the
command in the project's runtime rule. **Make every changed service run the current
working tree** with the project's rebuild or reload procedure: starting a stack reuses
an image built before the change. Do it again after every fix, and record how
freshness was established. An answering URL, or a HEAD that matches while changes are
uncommitted, does not establish it. Never override the stack cap or stop another
session's stack; at the cap, runtime verification is NOT RUN and the rest of VERIFY
carries on, and the PR names each flow NOT RUN with that reason.

**Empty the evidence directory** in every VERIFY, in scope or not:
`.claude/verify-evidence/` unless the project names another (inside the checkout,
ignored by git). It holds this VERIFY's screenshots, recordings and manifest only,
round N under `r<N>/`; an earlier VERIFY's files would otherwise reach the pull
request as if they were this change's.

## Step 2 - launch in one message

**a. Correctness review.**

- **The review runs on a model family other than the one that built the change.** A
  model reviewing its own family's output over-credits it (arxiv 2504.03846). Inside
  T3 Code (the `t3-code` MCP tools are available), call `orchestrator_capabilities`
  and pick an enabled instance of the other family by its `driverKind`: `codex` when
  Claude built the change, `claudeAgent` when Codex did. For a Codex reviewer, the
  reasoning option goes INSIDE `target` (the owner's setting; the catalog's default is
  lower), after checking that `orchestrator_capabilities` still lists that option and
  value:

  ```json
  {
    "target": {
      "providerInstanceId": "<the enabled instance>",
      "model": "<the first model it lists>",
      "options": [{ "id": "reasoningEffort", "value": "xhigh" }]
    },
    "mode": "async",
    "role": "review",
    "isolation": "shared"
  }
  ```

  `isolation: "shared"` because the reviewer must see uncommitted work. Add a
  `clientRequestId` unique to this round and a title such as
  `VERIFY review <slice> r1`. The task text is the body of
  `${CLAUDE_PLUGIN_ROOT}/agents/correctness-reviewer.md` without its frontmatter,
  followed by the review context. Keep the returned `taskId`.
- **Otherwise**, or when the delegated task fails to start: the `correctness-reviewer`
  agent with `model: "opus"` and the review context, and say in the summary that the
  review ran on the builder's own family.

**b. Runtime verification**, when in scope: the `runtime-verifier` agent with
`model: "opus"`. Give it the slice, the base, the stack URL, the profile, the
services that profile leaves out, how each changed service was made to run the
current code, and the round's evidence directory (`.claude/verify-evidence/r1/`). It
drives the harness's built-in browser when there is one (in T3 Code the `preview_*`
tools, a private profile, its own tab) and the project's browser tooling otherwise,
and saves a screenshot per verified criterion and one short recording per user flow
you named. Give it the list of flows. Launch it as soon as the stack answers and runs
that code. Do not hold the review back for the stack.

**c. Local validation**, in the background, on the current tree. With a `ship:check`
script in this checkout's `package.json`, run `pnpm ship:check`. Add `--add-tests`,
`--add-package` or `--add-checks` only with `--reason`, for a boundary the automatic
selection misses. Never narrow it. Without `ship:check`, follow
`${CLAUDE_PLUGIN_ROOT}/skills/ship/generic.md`, including its assertion-only sweep.
The reviewers do not write to the tree, so this run is valid unless a fix lands later.

Do not edit the tree while these run. Long commands use `run_in_background: true`.
Completions arrive as notifications, so never sleep-poll.

## Step 3 - judge the findings

You are the author. First compare the review's **Coverage** with the change and with
the scopes of the project's rules. An applicable rule it did not read, a changed file
it did not read, or a criterion without a traced production path makes the review
incomplete: send one more round for exactly what it skipped before you rely on its
verdict. Coverage adds up across rounds: earlier coverage stands for paths and
contracts the fixes did not touch, so a follow-up round needs coverage only of its
delta and of what that delta affects. Pass the previous Coverage with the follow-up
context. Then read every finding against the code.

- **Real:** fix it. A defect found by running the app first gets a failing regression
  test at the boundary that owns the behaviour; then the fix.
- **Not real:** reject it with a one-line reason (the scenario cannot occur because
  ...). Keep the reason for the summary and for the next round.
- **Style or speculation:** do not act on it.

## Step 4 - re-check only what changed

- **After a substantive product-code fix, whichever lane found it,** get a targeted
  correctness review of the fix and the callers it affects: a new `delegate_task` call
  (new `clientRequestId`) or a new agent, carrying the original brief, the slice, the
  base, the previous findings, your response to each, and the diff since the previous
  round. A fix to a runtime finding can introduce the race the runtime re-check does
  not exercise.
- **Re-run the runtime criteria** whose implementation or prerequisites the fix
  changed, on a stack running the fixed code, with the next round's evidence
  directory (`r2/`, `r3/`). A criterion's evidence is the newest round's that ran it.
- **Judge "trivial" by what the change can reach, not by its length.** A one-line
  change to authorization, persistence order, concurrency or workflow history is
  substantive. Only a fix that cannot change behaviour (a message string, a comment)
  skips the review.
- **Either way,** local validation runs again on the final tree.

## Step 5 - verdict

- **READY**: the review has no open blocker or major finding. For an observable
  change, runtime verification is READY with a recording of every user flow, or names
  each flow it could not record NOT RUN with its reason; for a change with nothing to
  observe, the reason it is out of scope is stated. Local validation exited `0` on the
  final tree.
- **NEEDS_FIX**: a real blocker or major finding is open, or validation failed (exit
  `1` names the step). Minor findings are listed in the summary with what you did
  about each.
- **BLOCKED**: validation refused (exit `2`) for a reason you cannot fix here, or no
  review could run at all. A failed Codex task falls back to the agent; it is not
  BLOCKED by itself.

An interrupted command or any other nonzero exit is not a pass. NOT RUN is never a
pass: it always appears in the summary to the user.

## After the verdict

Do not stop the stack yourself. Another session may share this worktree and be using
it; the project's session-end and idle hooks soft-stop it once no live session needs
it, which a stop from inside one session cannot know.

- **READY** -> write the evidence manifest (below), for every change; then
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py evidence` must exit 0 (a flow
  without its recording or reason is a VERIFY that is not done); then run
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify-state.py record`. It
  writes `.claude/.verify-state.json` with the verdict bound to the exact contents
  reviewed: every tracked and untracked, non-ignored file, by mode and blob id.
  `/ship`, `/quickship` and `/merge` run `verify-state.py check` before merging main
  and committing; committing the reviewed work keeps the verdict, and any other
  change to the contents makes the check name the changed paths for review. After
  merging main in they run `verify-state.py integrate`, which accepts the merge only
  when it is exactly git's clean merge and names anything else for review. A merge of main that applies
  cleanly does not re-arm VERIFY. A conflict resolved by hand that changes behaviour
  gets a targeted correctness review of the resolved paths and the contracts they
  touch before this verdict counts again.

  **Then open the PR for review, in this chat, without asking:**
  `${CLAUDE_PLUGIN_ROOT}/skills/ship/open-for-review.md`. It commits, integrates main,
  pushes the branch, opens (or updates) the PR with the evidence, links it to the
  thread, watches it, and tells the owner in one short message the PR URL and what to
  watch. Nothing merges, so it needs no approval. The owner approves on GitHub or
  types `/ship`; changes they ask for are fixed here, VERIFY runs again for them
  (step 4), and opening for review again updates the PR and its evidence. Tell the
  owner, in that same message, what was fixed, what you rejected and why, and
  anything NOT RUN.
- **NEEDS_FIX** -> fix in this chat, then step 4.
- **BLOCKED** -> surface it to the user. Do not ship.

### The evidence manifest

The verdict's screenshots and recordings, for the pull request the owner reviews.
Write `.claude/verify-evidence/manifest.json` (the evidence directory's root) at every
READY, from the verifier reports' evidence lines, keeping for each criterion the newest
round that ran it:

```json
{
  "verdict": "READY",
  "recordedAt": "<ISO 8601 time>",
  "slice": "<slice doc path or task>",
  "observable": true,
  "outOfScope": "",
  "flows": ["<user flow, as the slice words it>", "<another user flow>"],
  "items": [
    {
      "path": ".claude/verify-evidence/r1/01-sign-in.png",
      "kind": "screenshot",
      "criterion": "<the acceptance criterion, as the slice words it>",
      "caption": "<one sentence: what the reader sees>"
    },
    {
      "path": ".claude/verify-evidence/r2/02-upload-flow.mp4",
      "kind": "recording",
      "criterion": "<criterion>",
      "flow": "<user flow, as the slice words it>",
      "caption": "<sentence>"
    }
  ],
  "notRun": [{ "criterion": "<another user flow>", "reason": "<the missing service or reason>" }]
}
```

Paths are relative to the checkout, inside the evidence directory, ignored by git,
and every one must exist. `kind` is `screenshot` or `recording`; a recording names the
`flow` it shows. Every entry of `flows` has a recording, or a `notRun` entry whose
`criterion` is that flow, with its reason. A criterion runtime verification did not run
goes under `notRun` with its reason, so the pull request shows it beside the evidence;
NOT RUN is never a pass there either. A change with no observable effect writes
`"observable": false`, its reason in `outOfScope`, and no flows or items.
`verify-state.py evidence` checks all of this.
