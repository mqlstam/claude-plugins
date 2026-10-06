---
name: approve
description: Approve a release skill ahead of time (ship, quickship, merge, deploy, promote, release, hotfix), so Claude may run it once later in this session, for example inside a /goal run. Human-invoked only.
disable-model-invocation: true
argument-hint: "<skill> [<skill> ...]"
---

# Approve a release ahead of time

The user just typed `/approve $ARGUMENTS`. That typed line IS the approval: the
`release-approval-gate` hook reads it from the session transcript, where only the
user can write it. Nothing you write or run here creates or extends an approval.

What it allows:

- For each named release skill, you may invoke it ONCE in this session through the
  Skill tool. Its release action (merge, push to main, deploy tag, workflow
  dispatch) is then allowed once.
- A second run needs a second `/approve`. A different skill needs its own name:
  approving `deploy` does not approve `promote`.
- It does not carry over to another session or worktree.
- If the user types the skill themselves, that uses the approval up.

Do this now:

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/release-approval-gate.py" --status --session ${CLAUDE_SESSION_ID} --cwd "$PWD"`
   and report the result in one or two short lines, in the user's language:
   which skills are approved, and any name that is not a release skill here (it
   approved nothing). If the status does not yet show the approval, the
   transcript is written asynchronously: run it once more.
2. With no arguments, say the usage: `/approve ship deploy`.
3. Do not start any of the approved skills now unless the user's request already
   asks for it. The approval is for later in this session.
