#!/usr/bin/env python3
"""PreToolUse(Skill|Bash) gate: a release happens only when the USER approved it.

Two ways for the user to approve, both text the user typed:

  * type the release skill itself (/ship, /deploy, ..., or the namespaced
    /claude-tdd-workflow:ship);
  * ahead of time: `/approve <skill> [<skill> ...]`, after which the model may
    run each named skill in this session (e.g. inside a /goal run).

What counts as typed (owner, 2026-10-20, after review). A slash command Claude
Code ran (the `<command-name>` envelope, namespaced or not), or a message that
IS the approval: every non-empty line of it `/approve <skill> [<skill> ...]`,
every word a release skill, spaces around it allowed. An `/approve` line inside a
longer message is NOT an approval, because the gate cannot tell a message the
owner typed from one a harness injected: in T3 Code an owner's prompt and an
injected one (a `t3_thread_send`, a `delegate_task` notice) carry identical
transcript markers (`promptSource`, `turnOrigin`, `userType`), and an injected one
routinely quotes other text. Agents must never send `/approve` into another
thread. Never counted either: tool output, a background-task notice, a skill body
or reminder (`isMeta`), a compaction summary, a subagent, the model's text.

Two layers, one script:

  Skill arm  A skill whose SKILL.md declares `release-actions:` is a release
             skill. The model may invoke it while an approval of it is OPEN (its
             release has not taken effect yet). A skill without the key is
             untouched.

  Bash arm   The floor. A command that performs a release action is refused
             unless an open approval covers that action:

               merge       gh pr merge; gh api write to pulls/N/merge, merges
               push-main   git push whose destination is main/master
                           (incl. a bare push from main, --all, --mirror, a
                           wildcard refspec); gh api write to git/refs/heads/main
               deploy-tag  git push of a deploy-* tag, --tags, --follow-tags,
                           --mirror, a wildcard refspec; gh api write to
                           git/refs (other than a literal feature branch) or git/tags
               dispatch    gh workflow run, gh run rerun; gh api write to
                           actions/workflows/W/dispatches, actions/runs/N/rerun
               rerun       gh run rerun <id> --failed; gh api write to
                           actions/runs/N/rerun-failed-jobs

             Recognised on what each top-level simple command RUNS (round-5
             review): redirections removed wherever they stand, quotes and
             escapes resolved, then assignments, once-wrappers (env, command,
             exec, nice, time, sudo, nohup, timeout) and git's / gh's global
             options (`git -C/--git-dir`, `gh -R/--repo`, GH_REPO=) skipped.
             A shell given a literal command (`bash -lc '<s>'`, `sh -eo pipefail
             -c '<s>'`, `env X=1 bash -c`, a heredoc on a shell's standard input,
             also with `-s` and positional operands, or one started by another
             program, `bash scripts/wt.sh sh -c`) has that string read as a call
             of its own (rounds 6 and 9). The release environment (GH_REPO,
             GH_HOST, GIT_DIR, GIT_WORK_TREE, GIT_CONFIG*) set by `export`,
             `declare -x` or a plain assignment is carried through the rest of
             the call and into a shell string; a computed value is guarded (round
             9). A gh api call is judged by method and endpoint, never by
             payload, so `--input file` hides nothing. Refused, never guessed: a
             release in a compound command (for, while, until, if, a function),
             run by another program (xargs, find -exec, parallel, watch) or inside
             a shell's command string (run it directly); a computed command word;
             a git alias given with -c; a guarded push with any `-c` /
             `--config-env`, or with a GIT_CONFIG* variable set in the call
             (`push.followTags`, `push.default=matching` widen what one push
             updates); a guarded push without an explicit refspec (`git push`,
             `git push origin` go where git's configuration says); a PR or run URL
             and a -R / GH_REPO that name different repositories; and any git push
             option outside -u, -q, -v, -n, --porcelain, --no-verify and
             --force-with-lease with one ref (git takes `--branches` for `--all`
             and any unambiguous prefix such as `--tag`). Help is exempt only
             alone (`gh pr merge --help`).

             Which commands the arm guards (round-9 structural change): the
             SESSION decides first. When this session's own repository (the
             hook's cwd or CLAUDE_PROJECT_DIR) is named in
             release-approval-gate.json, every release-shaped command needs an
             approval, whatever repository it appears to target: merging an EABOS
             PR from an Endoxia session needs one too (owner, accepted). Reading a
             command's target can go wrong in ways not yet listed (four rounds
             found a quoted `cd`, a host prefix, an export, a PR URL); in a guarded
             session such a misreading now costs an approval, never a release. The
             transcript replay reads history with the same session, so those
             commands spend approvals too.

             In a session elsewhere the TARGET decides, and only ADDS guarding (a
             session reaching into a guarded repo). Round 10: the command is
             guarded when ANY repository it could act on is; no candidate replaces
             another. The candidates:
               * every directory the shell can be in: the session cwd moved by
                 each `cd` / `pushd` read from its unquoted words, where a `cd`
                 that can fail leaves the previous one a candidate (`;` runs the
                 next command either way; `&&` only after success); a `cd` in a
                 pipeline or the background moves nothing; `cd name` / `pushd name`
                 with CDPATH set may go to each CDPATH entry or stay relative (rounds
                 11-12; CDPATH is carried like the variables below, and `env -C` /
                 `git -C` never search it); then each directory after `env -C` or
                 `git -C`, and the ones passed on the way;
               * every GitHub remote of such a checkout (gh picks among them: a
                 fork's `upstream`, `gh repo set-default`), and, for gh, a
                 repository GIT_DIR / GIT_WORK_TREE / GIT_CONFIG* point it at;
               * a push's destination: a URL, or the checkout's remote of that
                 name through its own push URL (round 11);
               * a gh `-R` (the subcommand's or gh's own), GH_REPO whether set for
                 the command, by an earlier statement, exported or inherited from
                 the environment, until `env -u` / `env -i` / `env -` / `unset`
                 removes it or `export -n` / `declare +x` / `typeset +x` hides it
                 from gh (rounds 11-12); the repository of a PR or run URL
                 selector; an API path's `repos/<owner>/<repo>`. Every repository
                 name goes through one normaliser (`[HOST/]OWNER/REPO` or a URL;
                 the host is dropped).
             So `cd /work/endoxia; GH_REPO=mqlstam/EABOS; gh pr merge 42` is
             guarded (an unexported GH_REPO leaves gh in Endoxia), and so is
             `-R mqlstam/EABOS` from an Endoxia checkout. A directory, destination
             or repository known only when the command runs (`cd "$REPO"`, `cd -`,
             `popd`, `cd $(...)`, a missing checkout, GIT_DIR, `--git-dir`, `-R
             "$R"`, a computed GH_HOST, `git push ../mirror`) is guarded.

             ONE ANSWER (round-11 parity). The decision, the transcript replay and
             `--status` read a command through the same code: `classify`, the one
             scope factory `release_scope` (config + this session's directories),
             `branch_reader`, `inherited_env`. There is no second answer for
             history: a target that cannot be named is guarded in both. What can
             still differ is the world between the two reads (a checkout deleted
             or re-cloned since); `DecisionReplayParityTest` pins the rest.

             The Skill arm is not scoped. Without this arm the Skill arm is a
             signpost in front of an open door: in a /goal run on 2026-10-05 the
             model typed `gh pr merge` itself.

AN APPROVAL IS SPENT ONLY BY ITS EFFECT (owner, 2026-10-20). One approval covers
one effect of each action its skill declares, and stays open until that effect
has happened: the PR is MERGED, the deploy tag is on origin, the push to main
landed, the workflow run was created. Invoking the skill spends nothing. Measured
2026-09-04..10-20: about ten approvals spent while nothing was released (a tag
push that never reached origin, a merge that failed, a script that refused to
start, "invoking /deploy consumed the deploy approval"), 30 turns asking for a new
/approve, median 119 min each.

How the effect is read, when the next decision replays the transcript. The rule
is fail-closed: a command is charged unless it demonstrably did not take effect.

  * refused before it ran (a hook or permission refusal, the worktree guard):
    never charged;
  * merge and deploy-tag leave a fact GitHub keeps: the PR's state and merge
    commit (`gh pr view <PR>`), the tag on origin (`git ls-remote`). A MERGED PR
    or a present tag is charged, whatever the exit code said (`gh pr merge
    --delete-branch` exits 1 after a successful merge). A command that FAILED
    (non-zero exit, `failed` notice, or a background command that ended without
    a status) is refunded only when every PR / tag it names was written
    literally and is observably not merged / absent; a command that succeeded
    is charged even when nothing is visible yet (`--auto` merges later);
  * "ran alone" means one command besides plain `cd`s (round 12): a `cd` prints
    nothing a release's output could be mistaken for, and when it fails the
    release after `&&` does not run;
  * a push to main that failed is refunded only when it ran alone, had one
    destination, and git itself reported it did not move main (`! [rejected]`,
    `! [remote rejected]`, `src refspec ... does not match any`); a network
    failure after the server may have taken it is charged;
  * a dispatch or rerun that failed is refunded only when it ran alone and GitHub
    shows no run it could have made: no run of that workflow created since the
    attempt (a rerun: no newer attempt), read at least RUN_REGISTER_S after it;
  * a command still running, or without a recorded result, is charged;
  * one effect is one charge however often it is attempted: the same PR merged,
    the same tag pushed, IN THE SAME REPOSITORY, counts once (round 10: every
    effect, follow-on commit, run credit and used rerun is keyed by the normalised
    owner/repo; `deploy-T` in Endoxia and in EABOS are two effects); a second
    merge of another PR needs a second approval;
  * an effect is pinned to the ONE repository its candidates resolve to: every
    directory it may run in is resolved first, so `cd <root>; gh pr merge` from a
    subdirectory is that checkout's repository (round 12), and a checkout with
    several remotes is the one gh selects (`gh repo set-default`, stored as
    `remote.<name>.gh-resolved`). An effect whose repository cannot be pinned to
    one (the command fixes none with -R, a URL or an API path, and its directories
    are different repositories, git is pointed elsewhere, a GH_REPO may or may not
    reach gh, a push destination or a checkout's remotes cannot be resolved to
    exactly one) is charged with no
    lookup, no effect identity, no refund and no run credit: a lookup in one
    candidate proves nothing about the other, and could match an older effect
    there and dedupe this one away (round 11). Every lookup names the
    repository `Ledger.repo_of` resolved (`-R owner/repo`, `repos/owner/repo`),
    never gh's `{owner}/{repo}` or the cwd, and runs without the release
    environment;
  * GitHub that cannot be asked (or the lookup budget spent) is "took effect".

RELEASE COMMANDS ARE PLAIN WORDS (owner, 2026-10-20). Every release command, and
every `git push` or `gh api` write in a guarded repo, is judged only when each word
of it is a plain word (letters, digits, `._/:@+=,-`) or one of `>f`, `>>f`, `2>f`,
`&>f`, `2>&1` with a plain file. Quotes, `$`, backticks, globs, braces, a subshell,
`<`, any backslash in the call: refused, never interpreted. Commands are split at
`;`, `&&`, `||`, `|`, a lone `&` and newlines.

ONE RELEASE EFFECT PER CALL. A call that can have more than one release effect
(`gh pr merge 12 && gh pr merge 13`, two dispatches) is refused before it runs:
coverage is decided per call, and one approval is one effect. A release push
whose count git decides is refused the same way: `--tags`, `--follow-tags`,
`--mirror`, `--all`, a wildcard refspec, or more than one destination ref
(`git push origin main master`, `git push origin deploy-1 deploy-2`).

SHIP COVERS ITS OWN STAGING DEPLOY (owner, 2026-10-20). A skill may declare
`release-follow-on: deploy-tag`: its approval then also covers ONE deploy-* tag on
exactly the commit its own release produced, once that release took effect (the
PR's merge commit for /ship; for /quickship the commit `git push origin main`
reported, accepted only while it is on origin's main). Only the bare
`git push origin <tag>` qualifies, and the local tag's commit must be that commit:
a `src:dst` refspec pushes its source, whatever the local tag says. A tag on another commit, a
second tag, a promote/release/hotfix, all still need their own approval. /merge
declares no follow-on; `/approve merge deploy` covers both. Measured: 224 of 273
release starts left out the staging deploy and 53 then needed a second typed
deploy, median 96 min later.

A release that took effect also earns ONE `gh run rerun <id> --failed` of the run
it started, under the same approval: a deploy tag pushed by its literal name
(GitHub is asked whether run <id> is that tag's push), or a dispatch whose own
output printed the run's URL; the rerun must name that run's repository (its -R,
gh's -R, GH_REPO or cwd). Any other rerun is a dispatch.

Why the approval is read from the TRANSCRIPT and not from a file: a file the
gate trusts is a file the model can write. The effects are read from GitHub and
git, where the model's claims do not count.

What it does NOT buy, stated so nobody over-trusts it:
  * It guards against MISTAKES by well-meaning agents, not against a deliberately
    adversarial one (owner, 2026-10-21). Its rules are principles (what a command
    runs, an allowlisted shape, effects by endpoint) so a new spelling of a
    mistake is closed too, but it is not a sandbox.
  * A determined model can hide a release command (a script file, `sh -c` with
    a built string such as `bash -c "$CMD"`, a command piped into a shell, curl to
    the GitHub API). This turns SILENT into MUST-ACTIVELY-EVADE, which is what a
    guardrail against a cooperative agent needs to do.
  * Push configuration already stored in git (push.default, push.followTags,
    remote.<name>.push in the repo or the user's config) is not read: only what
    the command itself sets is refused.
  * Whatever Claude Code records as a user prompt counts as typed. A message
    another agent sends into this session through a harness (a T3 thread send)
    is recorded as one and cannot be told apart: a whole-message `/approve` sent
    that way would count. The rule for agents is never to send one.
  * Hooks are snapshotted at session start: an edit to this file takes effect
    in the next session (or after /reload-plugins).
  * An approval typed while Claude is busy is queued as a prompt, not run as a
    command; it still counts once recorded, if the message is only the approval.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from typing import NamedTuple

# The hook runs the script by path; tests load it with importlib under `python3 -I`,
# which does not put the script's directory on sys.path. Name it explicitly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_text import inert_regions, mask_inert, simple_commands  # noqa: E402
import agent_app  # noqa: E402

ACTION_CLASSES = ("merge", "push-main", "deploy-tag", "dispatch")

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

# The transcript is written asynchronously and "may not yet include the current
# turn's most recent messages when a hook fires" (code.claude.com/docs/en/hooks,
# common input fields). A skill the model invoked a moment ago may therefore be
# missing on the first read. Before refusing, the gate reads once more after
# this pause. Reasoned placeholder, not a measurement: a write is one appended
# JSON line, so a second is generous; re-derive if a refusal is ever observed
# that the re-read would have allowed.
TRANSCRIPT_LAG_RETRY_S = 1.0

# Cheap prefilter so the common Bash call never reads the transcript.
RELEASE_VERB_HINT = re.compile(r"\bgh\b|\bpush\b")

# The release skills, in the order a refusal suggests one for an action: the
# narrowest first, so a bare merge is told `/approve merge`, not `/approve ship`.
SUGGESTION_ORDER = ("merge", "quickship", "deploy", "promote", "release", "hotfix", "ship")


# --------------------------------------------------------------------------
# Skill resolution: which skills are release skills, and what they cover.
# --------------------------------------------------------------------------

def bare(name: str) -> str:
    return name.strip().lstrip("/").rsplit(":", 1)[-1].strip()


def _own_plugin_name() -> str | None:
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json")) as fh:
            return json.load(fh).get("name")
    except (OSError, ValueError):
        return None


def _installed_plugin_roots() -> dict[str, str]:
    roots: dict[str, str] = {}
    own = _own_plugin_name()
    if own:
        roots[own] = PLUGIN_ROOT
    path = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return roots
    for key, installs in (data.get("plugins") or {}).items():
        name = key.split("@", 1)[0]
        if name in roots or not installs:
            continue
        install_path = installs[0].get("installPath")
        if install_path:
            roots[name] = install_path
    return roots


def _lookup_env() -> dict[str, str]:
    """The environment every lookup runs in: this process's, without the variables
    that choose a repository or change git's configuration (round 11: an inherited
    GH_REPO or GIT_DIR redirected `gh` and `git -C` lookups to another repository)."""
    return {k: v for k, v in os.environ.items() if not _release_env(k)}


def _git_out(argv: list[str]) -> str | None:
    """stdout of a local git lookup, None when it failed or could not run."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=3, env=_lookup_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_toplevel(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3, env=_lookup_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _current_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True, text=True, timeout=3, env=_lookup_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def branch_reader(session_cwd: str, recorded: str | None = None):
    """THE branch of a push's checkout, for the decision and the replay alike (round-11
    parity): the session cwd's branch as the transcript recorded it when there is a
    record (the decision: none yet, so it is read now), any other checkout read now."""
    def branch_of(directory: str) -> str | None:
        if recorded is not None and directory == session_cwd:
            return recorded
        return _current_branch(directory)
    return branch_of


def _frontmatter_value(skill_md: str, key: str) -> str | None:
    try:
        with open(skill_md) as fh:
            text = fh.read()
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    front = text[3:end if end != -1 else len(text)]
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.+)$", front, re.M)
    return m.group(1).strip() if m else None


def _frontmatter_list(skill_md: str, key: str) -> tuple[str, ...]:
    raw = _frontmatter_value(skill_md, key)
    if not raw:
        return ()
    raw = raw.strip("[]")
    return tuple(sorted({
        v.strip().strip("'\"") for v in re.split(r"[,\s]+", raw) if v.strip().strip("'\"")
    }))


def read_release_actions(skill_md: str) -> tuple[str, ...]:
    return _frontmatter_list(skill_md, "release-actions")


def read_release_follow_on(skill_md: str) -> tuple[str, ...]:
    """The actions a skill's approval also covers ONCE, bound to the commit the
    skill's own release produced (`release-follow-on: deploy-tag` on /ship)."""
    return _frontmatter_list(skill_md, "release-follow-on")


class SkillResolver:
    """Maps a skill name (as typed, or as the Skill tool receives it) to the
    release actions its SKILL.md declares (empty = not a release skill)."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self._paths: dict[str, str | None] = {}
        self._cache: dict[tuple[str, str], tuple[str, ...]] = {}
        self._plugin_roots: dict[str, str] | None = None
        self._project_roots: list[str] | None = None

    def plugin_roots(self) -> dict[str, str]:
        if self._plugin_roots is None:
            self._plugin_roots = _installed_plugin_roots()
        return self._plugin_roots

    def project_roots(self) -> list[str]:
        if self._project_roots is None:
            roots = []
            for candidate in (_git_toplevel(self.cwd), self.cwd, os.environ.get("CLAUDE_PROJECT_DIR")):
                if candidate and candidate not in roots:
                    roots.append(candidate)
            self._project_roots = roots
        return self._project_roots

    def candidates(self, name: str) -> list[str]:
        name = name.strip().lstrip("/")
        if ":" in name:
            plugin, skill = name.split(":", 1)
            root = self.plugin_roots().get(plugin)
            return [os.path.join(root, "skills", skill, "SKILL.md")] if root else []
        paths = [os.path.join(r, ".claude", "skills", name, "SKILL.md") for r in self.project_roots()]
        paths.append(os.path.expanduser(f"~/.claude/skills/{name}/SKILL.md"))
        # A bare name in /approve may name a plugin skill ("/approve ship").
        paths += [os.path.join(r, "skills", name, "SKILL.md") for r in self.plugin_roots().values()]
        return paths

    def skill_md(self, name: str) -> str | None:
        key = name.strip().lstrip("/")
        if key not in self._paths:
            self._paths[key] = next((p for p in self.candidates(key) if os.path.isfile(p)), None)
        return self._paths[key]

    def _read(self, name: str, reader, kind: str) -> tuple[str, ...]:
        key = (kind, name.strip().lstrip("/"))
        if key not in self._cache:
            path = self.skill_md(key[1])
            self._cache[key] = reader(path) if path else ()
        return self._cache[key]

    def actions(self, name: str) -> tuple[str, ...]:
        return self._read(name, read_release_actions, "actions")

    def follow_on(self, name: str) -> tuple[str, ...]:
        return self._read(name, read_release_follow_on, "follow-on")


# --------------------------------------------------------------------------
# Bash classification.
# --------------------------------------------------------------------------

_DEPLOY_TAG_ASSIGN = re.compile(r"\b\w+=[\"']?deploy-")
_PUSH_FLAGS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
_MERGE_FLAGS_WITH_VALUE = {"-t", "--subject", "-b", "--body", "-F", "--body-file", "-A",
                           "--author-email", "--match-head-commit"}
_MAIN_BRANCHES = {"main", "master"}


class Ambiguous(Exception):
    """A command whose arguments cannot be read for certain."""


# Redirection operators, longest first: `&>>`, `&>`, `>>`, `>|`, `>&`, `<&`, `<<<`,
# `<<` / `<<-`, `<>`, `>`, `<`. An all-digit word right before one is its fd
# (`2>&1`); digits inside a word are not (`deploy-12>x` is `deploy-12`, `>x`).
_REDIR_OP = re.compile(r"&>>|&>|>>|>\||>&|<&|<<<|<<-?|<>|>|<")
_REDIR_WORD = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s;&|<>()"']+)+""")
_REDIR_FD = re.compile(r"\d+-?|-")


def _strip_redirects(raw: str) -> str:
    """`raw` with every redirection and its target blanked, wherever it stands:
    `git push origin a 2>&1 b` passes `a b` to git, and so must this. Read on the
    masked text, so a quoted `>` is no redirection. Raises Ambiguous for an
    operator without a target."""
    masked = mask_inert(raw)
    out = list(raw)
    for m in _REDIR_OP.finditer(masked):
        start, k = m.start(), m.end()
        j = start
        while j > 0 and masked[j - 1].isdigit():
            j -= 1
        if j < start and (j == 0 or masked[j - 1] in " \t"):
            start = j
        while k < len(masked) and masked[k] in " \t":
            k += 1
        target = (_REDIR_FD.match(masked, k) if m.group() in (">&", "<&") else None) \
            or _REDIR_WORD.match(masked, k)
        if not target:
            raise Ambiguous(f"`{m.group()}` without a target")
        for i in range(start, target.end()):
            out[i] = " "
    return "".join(out)


def _literal(value: str | None) -> bool:
    """A value written out in the command, not computed when it runs."""
    return bool(value) and not re.search(r"[$`*?]", value)


# --------------------------------------------------------------------------
# Scope: which repositories the Bash arm guards.
# --------------------------------------------------------------------------

# The owner's call (2026-10-05): the raw-command floor guards the repositories
# named in release-approval-gate.json and nothing else. Other repos keep their
# old freedom. The Skill arm is NOT scoped: a release skill invoked by the
# model needs an approval in every repo, which is the old disable-model-
# invocation behaviour plus the /approve option. A missing or unreadable
# config guards EVERY repo: the wrong answer is a refusal, never a release.
SCOPE_CONFIG = os.path.join(PLUGIN_ROOT, "release-approval-gate.json")

# A directory the command reaches only when it runs (`cd "$REPO"`, `cd -`, `popd`,
# `cd $(...)`, `GIT_DIR=...`): no path on disk, so every lookup in it is "unknown",
# and RepoScope guards it in the decision AND the replay (round-6 review). A relative
# change from it stays unresolved.
UNRESOLVED_DIR = "<a directory known only when the command runs>"


def _unresolved(path: str) -> bool:
    return path == UNRESOLVED_DIR or path.startswith(UNRESOLVED_DIR + "/")


def _chdir(base: str, arg: str | None) -> str:
    """The directory `cd <arg>` (or `git -C <arg>`, `env -C <arg>`) reaches from
    `base`, read from the unquoted word; None or a computed word is unresolved."""
    if not arg or arg == "-" or re.search(r"[$`*?\[]", arg):
        return UNRESOLVED_DIR
    arg = os.path.expanduser(arg)
    if os.path.isabs(arg):
        return os.path.normpath(arg)
    if _unresolved(base):
        return UNRESOLVED_DIR
    return os.path.normpath(os.path.join(base, arg))


_NOT_A_CD = object()


def _cd_target(words: list[str]):
    """Where one simple command moves the shell: `_NOT_A_CD`, or the argument `_chdir`
    resolves (None for a place known only when it runs: `popd`, `pushd` alone)."""
    i = 0
    while i < len(words) and (_ASSIGNMENT.match(words[i]) or words[i] in ("builtin", "command")):
        i += 1
    if i >= len(words) or words[i] not in ("cd", "pushd", "popd"):
        return _NOT_A_CD
    verb, args = words[i], words[i + 1:]
    if verb == "popd":
        return None
    while args and args[0].startswith("-") and args[0] != "-":
        if args.pop(0) == "--":
            break
    if not args:
        return "~" if verb == "cd" else None
    return args[0]


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def _dirs_of(where) -> tuple[str, ...]:
    """The directories an effect was recorded with: one path, or several (round 12)."""
    return where if isinstance(where, tuple) else (where,) if where else ()


def _first_dir(where) -> str:
    """A directory to run a local git lookup in: any of several of one repository
    (Ledger.repo_of pinned them to it) shares its refs and remotes."""
    dirs = _dirs_of(where)
    return dirs[0] if dirs else ""


def _cd_dirs(base: str, target, cdpath: str | None) -> list[str]:
    """Every directory `cd <target>` can reach from `base`. With CDPATH set, a bare
    name is looked up in each CDPATH entry first and in `base` last (rounds 11-12);
    CDPATH is the shell's, carried like the release environment, and `git -C` /
    `env -C` never search it."""
    if target is None:
        return [UNRESOLVED_DIR]
    here = _chdir(base, target)
    if not cdpath or not isinstance(target, str) or target.startswith(("/", "./", "../", "~")) \
            or target in (".", ".."):
        return [here]
    if _computed(cdpath):
        return [UNRESOLVED_DIR]
    return _unique([*(_chdir(_chdir(base, entry or "."), target) for entry in cdpath.split(":")), here])


def slug_of_url(url: str) -> str | None:
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


_GITHUB_REPO_URL = re.compile(r"https?://(?:www\.)?github\.com/([^/\s?#]+)/([^/\s?#]+)(?:[/?#].*)?", re.I)


def slug_of_selector(word: str | None) -> str | None:
    """owner/repo of a GitHub URL given as a PR or run selector
    (`https://github.com/EndoxiaBV/Endoxia/pull/42`): gh acts on THAT repository."""
    m = _GITHUB_REPO_URL.fullmatch(word or "")
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}".lower() if m else None


def _norm_slug(slug: str) -> str:
    """owner/repo of what gh takes as a repository: `OWNER/REPO`, `HOST/OWNER/REPO`
    or a URL (round-9 review: `-R github.com/EndoxiaBV/Endoxia` kept its host and
    matched no guarded repo). Any host is dropped: a guarded name on another host
    is guarded too, which only refuses more."""
    url = slug_of_selector(slug.strip())
    if url:
        return url
    parts = [p for p in re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", slug.strip()).split("/") if p]
    if len(parts) >= 3:
        parts = parts[1:3]
    return "/".join(parts).lower().removesuffix(".git")


def inherited_env() -> dict[str, str]:
    """The repository variables the Bash tool's shell inherits from Claude Code's
    environment, which the hook shares (round 10: an inherited GH_REPO is a candidate
    too). Only the ones that name a repository: they can only add guarding."""
    return {k: os.environ[k] for k in ("GH_REPO", "GIT_DIR", "GIT_WORK_TREE", "CDPATH") if os.environ.get(k)}


def session_dirs(payload: dict) -> tuple[str, ...]:
    """The directories that name this session's own repository: the hook's cwd and
    CLAUDE_PROJECT_DIR, the two the gate already reads as "this project"."""
    return tuple(d for d in (payload.get("cwd") or os.getcwd(), os.environ.get("CLAUDE_PROJECT_DIR")) if d)


def release_scope(cwd: str) -> "RepoScope":
    """THE scope of a decision, its replay and `--status` (round-11 parity): the
    config's guarded repositories and this session's directories, nothing else."""
    return RepoScope.from_config(session_dirs({"cwd": cwd}))


_REMOTE_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[\w.-]+@[\w.-]+:")


# gh's order of remotes (cli/cli context/remote.go, remoteNameSortScore).
_GH_REMOTE_ORDER = {"upstream": 3, "github": 2, "origin": 1}


def checkout_repositories(directory: str, run_git=_git_out) -> tuple[frozenset[str], str | None]:
    """(every GitHub repository the checkout's remotes name, the one gh selects).

    gh picks among the remotes (round 11), so each is a scope candidate. Which one it
    acts on is determinate when `gh repo set-default` stored a default: git config
    `remote.<name>.gh-resolved` = `base` (that remote's repository) or
    `[HOST/]OWNER/REPO`, the first in gh's order of remotes wins (cli/cli
    context/context.go BaseRepo; observed with gh 2.102.1). With one remote it is
    that one. Otherwise None: gh would prompt, or fall back to its first remote
    without one; the effect stays unpinned rather than guess (round 12)."""
    if not directory or _unresolved(directory) or not os.path.isdir(directory):
        return frozenset(), None
    by_name: dict[str, str] = {}
    for line in (run_git(["git", "-C", directory, "remote", "-v"]) or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and (slug := slug_of_url(parts[1])):
            by_name.setdefault(parts[0], slug)
    resolved: list[tuple[int, str]] = []
    for line in (run_git(["git", "-C", directory, "config", "--get-regexp",
                          r"^remote\..*\.gh-resolved$"]) or "").splitlines():
        key, _, value = line.partition(" ")
        name = key[len("remote."):-len(".gh-resolved")]
        if name not in by_name or not value.strip():
            continue  # gh lists only remotes on GitHub
        repo = by_name[name] if value.strip() == "base" else _repo_path(value.strip())
        if repo:
            resolved.append((_GH_REMOTE_ORDER.get(name.lower(), 0), repo))
    slugs = frozenset(by_name.values()) | {repo for _rank, repo in resolved}
    if resolved:
        top = max(rank for rank, _repo in resolved)
        chosen = {repo for rank, repo in resolved if rank == top}
        return slugs, (next(iter(chosen)) if len(chosen) == 1 else None)
    return slugs, (next(iter(slugs)) if len(slugs) == 1 else None)


def remote_slugs(directory: str, run_git=_git_out) -> frozenset[str]:
    """Every GitHub repository a checkout can make gh act on: its scope candidates."""
    return checkout_repositories(directory, run_git)[0]


def remote_slug(directory: str, remote: str | None, run_git=_git_out) -> str | None:
    """The repository a push destination names: a URL itself, or a remote of the
    checkout through its own push URL (round 11). None when it cannot be resolved: a
    path, a computed word, an unknown remote, a host that is not GitHub."""
    if not remote or _computed(remote):
        return None
    if _REMOTE_URL.match(remote):
        return slug_of_url(remote)
    if remote.startswith(("/", ".", "~")) or not directory or _unresolved(directory) \
            or not os.path.isdir(directory):
        return None
    out = run_git(["git", "-C", directory, "remote", "get-url", "--push", remote])
    return slug_of_url(out) if out else None


class RepoScope:
    """Decides whether a release command is guarded.

    The SESSION first (round-9 structural change): when the session's own
    repository (`session_dirs`: the hook cwd, CLAUDE_PROJECT_DIR) is guarded, every
    release-shaped command is, whatever repository it appears to target, in the
    decision and the replay alike. Reading a target can be wrong in ways nobody has
    listed yet (a host prefix, an export, a quoted `cd`); in a guarded session such
    a mistake now costs an approval, never a release.

    Otherwise the TARGET decides, and only adds guarding: a session elsewhere that
    reaches into a guarded repository. A target that cannot be named is guarded, in
    the decision and the replay alike (round-11 parity: there is no second answer for
    history; a deleted clone's release costs an approval, never a release)."""

    def __init__(self, guarded: set[str] | None, slugs_of_dir=remote_slugs, session_dirs=(),
                 slug_of_remote=remote_slug):
        self.session_dirs = tuple(d for d in session_dirs if d)
        self.guarded = guarded  # None = every repo
        self._slugs_of_dir = slugs_of_dir
        self._slug_of_remote = slug_of_remote
        self._cache: dict[str, frozenset[str]] = {}
        self._remotes: dict[tuple[str, str], str | None] = {}
        self._session: bool | None = None

    @classmethod
    def from_config(cls, session_dirs=()) -> "RepoScope":
        try:
            with open(SCOPE_CONFIG) as fh:
                repos = json.load(fh).get("guardedRepos")
            guarded = {str(r).lower() for r in repos} if isinstance(repos, list) else None
        except (OSError, ValueError, AttributeError):
            guarded = None
        return cls(guarded, session_dirs=session_dirs)

    def dir_slugs(self, path: str) -> frozenset[str]:
        if path not in self._cache:
            self._cache[path] = frozenset(self._slugs_of_dir(path))
        return self._cache[path]

    def remote_slug(self, path: str, remote: str) -> str | None:
        if (path, remote) not in self._remotes:
            self._remotes[(path, remote)] = self._slug_of_remote(path, remote)
        return self._remotes[(path, remote)]

    def session_guarded(self) -> bool:
        """Whether this session's own repository is guarded (read once)."""
        if self._session is None:
            self._session = self.guarded is None or any(
                self.dir_slugs(d) & self.guarded for d in self.session_dirs)
        return self._session

    def guards(self, run_dirs, *slugs: str | None, also_dirs=(), remotes=()) -> bool:
        """Whether a command is guarded: the UNION of every repository it could act
        on (round 10). `run_dirs`: every directory it may run in (a `cd` that can fail
        leaves the shell where it was); `also_dirs`: the directories it passed on the
        way (before `env -C` / `git -C`); `slugs`: every repository it names (-R,
        GH_REPO however set, a URL, an API path); `remotes`: (directory, destination)
        of a push. A named candidate only adds: it never unguards a guarded cwd. A
        directory or destination that cannot be read is guarded (round 11: the same
        answer in the decision and the replay)."""
        if self.guarded is None or self.session_guarded():
            return True
        run_dirs = [run_dirs] if isinstance(run_dirs, str) else list(run_dirs)
        named = [s for s in slugs if s]
        if any(_computed(s) or _norm_slug(s) in self.guarded for s in named):
            return True  # a computed one (`-R "$R"`) may be the guarded repo
        for directory, remote in remotes:
            slug = self.remote_slug(directory, remote)
            if slug is None or slug in self.guarded:
                return True  # a destination that cannot be resolved may be the guarded repo
            named.append(slug)
        for d in (*run_dirs, *also_dirs):
            if _unresolved(d) or self.dir_slugs(d) & self.guarded:
                return True
        if named:
            return False  # a place no repository is read for adds nothing to a named one
        return any(not self.dir_slugs(d) for d in run_dirs)


GUARD_ALL = RepoScope(None)


def _stops_at_pipe(masked: str, start: int) -> bool:
    """Whether the simple command at `start` sends its output into a pipe (`|` or
    `|&`). A redirect's `&` (`2>&1`, `&>file`) belongs to the command; `||` and `&&`
    and `;` end it without a pipe. Read on the masked text, so a quoted `|` is not
    one."""
    i, n = start, len(masked)
    while i < n:
        c = masked[i]
        if c in ";\n":
            return False
        if c == "|":
            if i > start and masked[i - 1] == ">":  # `>|file` is a redirection
                i += 1
                continue
            return masked[i + 1:i + 2] != "|"
        if c == "&":
            if masked[i + 1:i + 2] == "&":
                return False
            if i > start and masked[i - 1] in "<>":  # 2>&1, <&3
                i += 1
                continue
            if masked[i + 1:i + 2] == ">":  # &>file
                i += 2
                continue
            return False  # a background job ends the command
        i += 1
    return False


# The plain release shape (owner decision 2026-10-20, after four reviews found
# shell constructs that parsing misread: `2>&1 --tags`, `>/tmp/log\\> --tags`, an
# escaped `;`). A release command is judged only when every word of it is a plain
# word or one of a closed list of plainly written redirections; anything else is
# refused, so the gate never has to interpret shell to count a release.
_PLAIN_WORD = re.compile(r"[A-Za-z0-9._/:@+=,-]+")
_PLAIN_REDIRECT = re.compile(r"(?:>>?|2>|&>)[A-Za-z0-9._/:@+=,-]+|2>&1")
_REDIRECT_OPERATORS = {">", ">>", "2>", "&>"}
RELEASE_SHAPE_EXAMPLE = "`git push origin deploy-<TS>`"


def release_shape_problem(cmd: str, starts: list[int]) -> str | None:
    """Why the release commands starting at `starts` are not in the plain shape,
    or None. Commands are split at `;`, `&&`, `||`, `|`, a lone `&` and newlines."""
    if not starts:
        return None
    if "\\" in cmd:
        return "a backslash (an escape or a line continuation)"
    masked = mask_inert(cmd)
    commands = simple_commands(masked)
    for k, (s, e, sep) in enumerate(commands):
        if not any(s <= p < e for p in starts):
            continue
        before = commands[k - 1][2] if k else ""
        if before in ("(", ")") or sep in ("(", ")"):
            return "a subshell or command substitution around it"
        words = cmd[s:e].split()
        i = 0
        while i < len(words):
            word = words[i]
            if word in _REDIRECT_OPERATORS:
                if i + 1 >= len(words) or not _PLAIN_WORD.fullmatch(words[i + 1]):
                    return f"the redirection `{word}` without a plain file after it"
                i += 2
                continue
            if not (_PLAIN_WORD.fullmatch(word) or _PLAIN_REDIRECT.fullmatch(word)):
                return f"`{word}`"
            i += 1
    return None


def _help_only(args: list[str]) -> bool:
    """`gh pr merge --help`, `git push -h`: the help flag is the ONLY argument.
    `gh pr merge 42 --body --help` merges (round-5 review)."""
    return args in (["--help"], ["-h"])


def _rerun_of_one_run(args: list[str]) -> tuple[str, str | None] | None:
    """(run id, -R slug) when the arguments are exactly `<id> --failed` (optionally
    `-R owner/repo`): a re-run of the failed jobs of one named run. Any other form
    (all jobs, `--job`, `--debug`, an id computed at runtime) is a plain dispatch."""
    run_id, slug, failed = None, None, False
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--failed":
            failed = True
        elif tok in ("-R", "--repo") and i + 1 < len(args):
            slug = args[i + 1]
            i += 1
        elif tok.startswith("--repo="):
            slug = tok.split("=", 1)[1]
        elif re.fullmatch(r"\d+", tok) and run_id is None:
            run_id = tok
        else:
            return None
        i += 1
    return (run_id, slug) if run_id and failed else None


def _match_head(args: list[str]) -> str | None:
    """The commit `gh pr merge --match-head-commit <SHA>` lets GitHub merge, or None."""
    for i, tok in enumerate(args):
        if tok == "--match-head-commit" and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith("--match-head-commit="):
            return tok.split("=", 1)[1]
    return None


def _merge_target(args: list[str]) -> tuple[str | None, str | None, bool]:
    """(PR ref, -R slug, --auto) of `gh pr merge <args>`. No ref means the current
    branch's PR, which the replay cannot name."""
    ref, slug, auto = None, None, False
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-R", "--repo"):
            slug = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if tok in _MERGE_FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("--repo="):
            slug = tok.split("=", 1)[1]
        elif tok == "--auto":
            auto = True
        elif not tok.startswith("-") and ref is None:
            ref = tok
        i += 1
    return ref, slug, auto


_WORKFLOW_RUN_FLAGS_WITH_VALUE = {"-f", "--raw-field", "-F", "--field", "-r", "--ref", "--json"}


def _workflow_target(args: list[str]) -> tuple[str | None, str | None]:
    """(workflow, -R slug) of `gh workflow run <args>`."""
    workflow, slug = None, None
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-R", "--repo"):
            slug = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if tok in _WORKFLOW_RUN_FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("--repo="):
            slug = tok.split("=", 1)[1]
        elif not tok.startswith("-") and workflow is None:
            workflow = tok
        i += 1
    return workflow, slug


# --------------------------------------------------------------------------
# What each simple command runs: a normalised view (round-5 review).
# --------------------------------------------------------------------------
#
# A release is recognised on the words the program receives, not on the text:
# redirections removed wherever they stand, quotes and escapes resolved (shlex),
# then the command's own prefix skipped (assignments, wrappers that run their
# command once, git's and gh's global options) before the subcommand is read. So
# `git > /tmp/x push origin main`, `g\it push origin main` and
# `gh -R EndoxiaBV/Endoxia pr merge 42` are all the release they run. What the
# view cannot place is refused, never allowed: a command word computed when it
# runs, a release run by another program (`xargs`, `find -exec`, `parallel`,
# `echo`), and any release in a call that uses a compound command (`for`,
# `while`, `if`, `{ }`, a function): only a top-level simple command may release.

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_COMPUTED = re.compile(r"[$`*?\[]")
# Words that open or continue a compound command in command position.
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
                   "case", "esac", "select", "function", "{", "}", "!", "[[", "]]", "coproc"}
# Programs that run the command after them ONCE, with the options each takes a value for.
# The plugin's App wrapper (agent-github-token.py): see View.via_app.
APP_WRAPPER = "agent-github-token.py"

_ONCE_WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "command": set(), "builtin": set(), "nohup": set(),
    "exec": {"-a"},
    "nice": {"-n", "--adjustment"},
    "time": {"-o", "--output", "-f", "--format"},
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "-D", "--chdir"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
}
_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
                          "--super-prefix", "--attr-source"}
_GH_GLOBAL_WITH_VALUE = {"-R", "--repo", "--hostname"}
_GH_SUBCOMMANDS = {("pr", "merge"): "gh-pr-merge", ("workflow", "run"): "gh-workflow-run",
                   ("run", "rerun"): "gh-run-rerun"}
# Wrapper options that start the program in another directory.
_WRAPPER_CHDIR = {("env", "-C"), ("env", "--chdir"), ("sudo", "-D"), ("sudo", "--chdir")}
# Environment that points git at another repository (round-6 review): unresolved.
_GIT_REPO_ENV = {"GIT_DIR", "GIT_WORK_TREE"}


def _release_env(name: str) -> bool:
    """A variable that changes which repository a release reaches, or what a push
    updates: carried through the rest of a call once set (round-9 review)."""
    return name in ("GH_REPO", "GH_HOST", "CDPATH") or name in _GIT_REPO_ENV or name.startswith("GIT_CONFIG")

# Shells that run a literal command string (`bash -c '<string>'`), or their standard
# input when given no operand or `-s` (`bash <<'EOF'`, `bash -s -- 42 <<'EOF'`).
# Round-6 review: the string is read like a call of its own, and a release in it is
# refused.
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_SHELL_LONG_WITH_VALUE = {"--rcfile", "--init-file"}


class View(NamedTuple):
    kind: str | None  # git-push | gh-pr-merge | gh-workflow-run | gh-run-rerun | gh-api | shell
    args: list[str]  # the words after the subcommand; for a shell, [its name]
    git_dirs: list[str | None]  # git -C values, in order; None = a repository named when it runs
    slug: str | None  # gh -R / --repo
    problem: str | None  # why this release cannot be judged
    compound: bool  # a shell keyword stands in command position
    chdirs: tuple[str | None, ...] = ()  # wrapper directory changes (env -C), in order
    payload: str | None = None  # a shell's `-c` string; None = it reads its standard input
    runner: str | None = None  # the program that runs this shell further on
    # The command's own environment, in order: ("set", NAME, value) for `NAME=v gh ...`
    # and `env NAME=v`, ("unset", NAME, None) for `env -u NAME`, ("clear", None, None)
    # for `env -i` / `env -` (round 11).
    env: tuple[tuple[str, str | None, str | None], ...] = ()
    # Run through the plugin's agent-github-token.py (`python3 .../agent-github-token.py
    # run <git|gh> ...`), which pushes a branch or writes PR text as the agent's GitHub
    # App and refuses anything else. What it runs is judged as itself; a release
    # through it is refused (owner, 2026-10-22).
    via_app: bool = False


def _shell_run(words: list[str], j: int) -> tuple[str, str | None] | None:
    """(shell name, its `-c` string or None for standard input) when `words[j]` is a
    shell that runs a literal command: `bash -lc '<s>'`, `sh -eo pipefail -c '<s>'`,
    or a shell with options only (`bash`) or with `-s`, whose operands are positional
    arguments (round-9 review: `bash -s -- 42 <<'EOF'`), which runs its standard input.
    A shell given a script operand (`bash scripts/x.sh`) is None."""
    name = os.path.basename(words[j])
    if name not in _SHELLS:
        return None
    k, has_c, has_s = j + 1, False, False
    while k < len(words):
        tok = words[k]
        if tok == "--":
            k += 1
            break
        if tok in _SHELL_LONG_WITH_VALUE:
            k += 2
        elif tok.startswith("--"):
            k += 1
        elif len(tok) > 1 and tok[0] in "-+":
            has_c = has_c or (tok[0] == "-" and "c" in tok[1:])
            has_s = has_s or (tok[0] == "-" and "s" in tok[1:])
            k += 1 + sum(tok[1:].count(o) for o in "oO")  # `-o pipefail`, `-eo pipefail`
        else:
            break
    if has_c:
        return (name, words[k]) if k < len(words) else None
    return (name, None) if has_s or k >= len(words) else None


def _git_view(rest: list[str]) -> tuple[list[str], list[str | None], str | None] | None:
    """(args, repository dirs, problem) when `git <rest>` is a push."""
    i, dirs, problem, config = 0, [], None, None
    while i < len(rest) and rest[i].startswith("-"):
        opt, eq, value = rest[i].partition("=")
        if opt in _GIT_GLOBAL_WITH_VALUE and not eq:
            value = rest[i + 1] if i + 1 < len(rest) else ""
            i += 2
        else:
            i += 1
        if opt == "-C":
            dirs.append(value)
        elif opt in ("--git-dir", "--work-tree"):
            dirs.append(None)  # the repository is wherever that names: unresolved
        if opt in ("-c", "--config-env"):
            config = config or (f"{opt}={value}" if eq else f"{opt} {value}")
            if opt == "-c" and value.lower().startswith("alias."):
                problem = f"a git alias defined on the command line (`-c {value}`)"
    if i < len(rest) and rest[i] == "push":
        if config and not problem:
            # Round-6 review: `-c push.followTags=true` pushes tags beside main,
            # `-c push.default=matching` updates main from a feature branch. Which
            # setting widens a push is git's business, so none is accepted.
            problem = (f"git configuration given on the command line (`{config}`), which can "
                       f"change what a push updates")
        return rest[i + 1:], dirs, problem
    if problem and i < len(rest):
        return rest[i + 1:], dirs, problem  # `git -c alias.p=push p ...`: cannot tell
    return None


# gh's short options that take a value, which pflag also accepts attached (`-XPOST`).
_GH_SHORT_WITH_VALUE = set("RXfFHqtpbAr")


def _split_short(args: list[str]) -> list[str]:
    out: list[str] = []
    for tok in args:
        if len(tok) > 2 and tok[0] == "-" and tok[1] in _GH_SHORT_WITH_VALUE:
            out += [tok[:2], tok[2:]]
        else:
            out.append(tok)
    return out


def _gh_view(rest: list[str], slug: str | None) -> tuple[str, list[str], str | None] | None:
    """(kind, args, slug) when `gh <rest>` is a merge, dispatch, rerun or api call."""
    rest = _split_short(rest)
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        opt, eq, value = rest[i].partition("=")
        if opt in _GH_GLOBAL_WITH_VALUE and not eq:
            value = rest[i + 1] if i + 1 < len(rest) else ""
            i += 2
        else:
            i += 1
        if opt in ("-R", "--repo"):
            slug = value
    sub = rest[i:]
    if sub[:1] == ["api"]:
        return "gh-api", sub[1:], slug
    kind = _GH_SUBCOMMANDS.get(tuple(sub[:2]))
    return (kind, sub[2:], slug) if kind else None


def _tool_view(tool: str, rest: list[str]) -> View | None:
    if tool == "git":
        got = _git_view(rest)
        return View("git-push", got[0], got[1], None, got[2], False) if got else None
    if tool == "gh":
        got = _gh_view(rest, None)
        return View(got[0], got[1], [], got[2], None, False) if got else None
    return None


def command_view(words: list[str]) -> View:
    """What one simple command runs, as far as releases go. The environment it sets
    for itself is in `env`; classify() adds what earlier commands of the call set."""
    i, compound, chdirs, env = 0, False, [], []

    def assignment(w: str) -> None:
        name, _, value = w.partition("=")
        env.append(("set", name, value))

    while i < len(words):
        w = words[i]
        if w in _SHELL_KEYWORDS:
            compound = True
            i += 1
        elif _ASSIGNMENT.match(w):
            assignment(w)
            i += 1
        elif os.path.basename(w) in _ONCE_WRAPPERS:
            wrapper = os.path.basename(w)
            takes = _ONCE_WRAPPERS[wrapper]
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGNMENT.match(words[i])):
                tok = words[i]
                if _ASSIGNMENT.match(tok):
                    assignment(tok)
                opt, eq, value = tok.partition("=")
                if (wrapper, opt) in _WRAPPER_CHDIR:
                    chdirs.append(value if eq else (words[i + 1] if i + 1 < len(words) else None))
                if wrapper == "env" and opt in ("-u", "--unset"):
                    env.append(("unset", value if eq else (words[i + 1] if i + 1 < len(words) else ""), None))
                if wrapper == "env" and tok in ("-i", "--ignore-environment", "-"):
                    env.append(("clear", None, None))
                i += 2 if tok in takes else 1
            if wrapper == "timeout" and i < len(words):
                i += 1  # the duration
        else:
            break
    own = {"compound": compound, "chdirs": tuple(chdirs), "env": tuple(env)}
    none = View(None, [], [], None, None, **own)
    if i >= len(words):
        return none
    head, rest = words[i], words[i + 1:]
    if (os.path.basename(head) in ("python3", "python") and len(rest) >= 3
            and os.path.basename(rest[0]) == APP_WRAPPER and rest[1] == "run" and not _COMPUTED.search(rest[0])):
        view = _tool_view(os.path.basename(rest[2]), rest[3:])
        if view:
            via = None if view.kind == "git-push" else (
                f"a release run through {APP_WRAPPER}, which runs only review work (a branch push, PR text)")
            return view._replace(via_app=True, problem=view.problem or via, **own)
    if _COMPUTED.search(head):
        problem = f"a command word computed when it runs (`{head}`)"
        # `$G push ...` may be git, `$(which gh) pr merge` may be gh: either is refused.
        for tool in ("git", "gh"):
            view = _tool_view(tool, rest)
            if view:
                return view._replace(problem=problem, **own)
        for j in range(i, len(words)):
            name = os.path.basename(re.sub(r"[^\w./-]", "", words[j]))
            view = _tool_view(name, words[j + 1:])
            if view:
                return view._replace(problem=problem, **own)
        return none
    view = _tool_view(os.path.basename(head), rest)
    if view:
        return view._replace(**own)
    shell = _shell_run(words, i)
    if shell:
        return View("shell", [shell[0]], [], None, None, payload=shell[1], **own)
    # Not git/gh in command position: a release further on runs under a program the
    # gate does not model (xargs, find -exec, parallel, watch, echo ...), or in a
    # shell string that program starts (`bash scripts/wt.sh sh -c '...'`).
    for j in range(i + 1, len(words)):
        view = _tool_view(os.path.basename(words[j]), words[j + 1:])
        if view:
            return view._replace(problem=f"a release run by `{os.path.basename(head)}`", **own)
        shell = _shell_run(words, j)
        if shell and shell[1] is not None:
            return View("shell", [shell[0]], [], None, None, payload=shell[1],
                        runner=os.path.basename(head), **own)
    return none


_PUSH_OPTIONS_ALLOWED = {"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--porcelain",
                         "-n", "--dry-run", "--no-verify", "--force-with-lease"}
_PUSH_OPTIONS_TEXT = "-u, -q, -v, -n, --porcelain, --no-verify, --force-with-lease with one ref"


def _push_option_problem(args: list[str]) -> str | None:
    """Round-5 review: git takes `--branches` for `--all` and any unambiguous prefix of a
    long option (`--tag`, `--mir`), so options are allowlisted, never denylisted."""
    for tok in args:
        if tok.startswith("-") and tok != "-" and tok.split("=", 1)[0] not in _PUSH_OPTIONS_ALLOWED:
            return f"the push option `{tok}` (allowed: {_PUSH_OPTIONS_TEXT})"
    if any(t.split("=", 1)[0] == "--force-with-lease" for t in args):
        refs = [t for t in args if not t.startswith("-")][1:]
        if len(refs) != 1 or "*" in refs[0]:
            return "`--force-with-lease` without exactly one explicit ref"
    return None


# gh api: which endpoint a write reaches, and what release that is (round-5 review:
# by endpoint and method, never by payload, so `--input file` hides nothing).
_API_VALUE_FLAGS = {"-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header",
                    "--input", "-q", "--jq", "-t", "--template", "--cache", "--hostname", "-p",
                    "--preview"}
_API_FIELD_FLAGS = {"-f", "--raw-field", "-F", "--field"}


def _api_request(args: list[str]) -> tuple[str, str | None, dict[str, str], bool]:
    """(method, endpoint, fields, has --input) of `gh api <args>`."""
    method, endpoint, fields, has_input = None, None, {}, False
    i = 0
    while i < len(args):
        opt, eq, value = args[i].partition("=") if args[i].startswith("--") else (args[i], "", "")
        if opt in _API_VALUE_FLAGS:
            if not eq:
                value = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            if opt in ("-X", "--method"):
                method = value.upper()
            elif opt in _API_FIELD_FLAGS:
                key, _, val = value.partition("=")
                fields[key] = val
            elif opt == "--input":
                has_input = True
        elif not args[i].startswith("-") and endpoint is None:
            endpoint = args[i]
        i += 1
    if method is None:
        method = "POST" if fields or has_input else "GET"
    return method, endpoint, fields, has_input


def _api_effect(endpoint: str, fields: dict[str, str]) -> tuple[str, str | None] | None:
    """(class, the PR / workflow / run it names) of a WRITE to `endpoint`, or None."""
    path = endpoint.split("?", 1)[0].rstrip("/")
    if m := re.search(r"(?:^|/)pulls/([^/]+)/merge$", path):
        return "merge", m.group(1)
    if re.search(r"(?:^|/)merges$", path):
        return "merge", None
    if m := re.search(r"(?:^|/)actions/workflows/([^/]+)/dispatches$", path):
        return "dispatch", m.group(1)
    if m := re.search(r"(?:^|/)actions/runs/([^/]+)/rerun-failed-jobs$", path):
        return "rerun", m.group(1)
    if m := re.search(r"(?:^|/)actions/(?:runs|jobs)/([^/]+)/rerun$", path):
        return "dispatch", None
    if m := re.search(r"(?:^|/)git/refs/heads/(.+)$", path):
        return ("push-main", None) if m.group(1) in _MAIN_BRANCHES else None
    if re.search(r"(?:^|/)git/refs$", path):
        ref = fields.get("ref", "")
        if ref.startswith("refs/heads/") and _literal(ref):
            return ("push-main", None) if ref[len("refs/heads/"):] in _MAIN_BRANCHES else None
        return "deploy-tag", None  # a tag, or a ref only the payload names
    if re.search(r"(?:^|/)git/(?:refs/.+|tags(?:/.*)?)$", path):
        return "deploy-tag", None
    return None


_LIST_DETAILS = ("tags", "tag_dirs", "tag_remotes", "merges", "push_dirs", "push_remotes", "reruns",
                 "tag_plain", "dispatches", "unbounded_why")


def _merge_details(outer: dict, inner: dict) -> None:
    """Add what a shell's `-c` string releases to the call that runs it, so the
    replay charges it like the release itself."""
    for key in _LIST_DETAILS:
        outer[key] += inner[key]
    outer["effects"] += inner["effects"]
    outer["tags_literal"] = outer["tags_literal"] and inner["tags_literal"]
    outer["push_single"] = outer["push_single"] and inner["push_single"]
    outer["unbounded"] = outer["unbounded"] or inner["unbounded"]
    outer["sole"] = outer.get("sole", False) and inner.get("sole", False)


def _rerun_selector(args: list[str]) -> str | None:
    """The run `gh run rerun <args>` names (its first operand)."""
    i = 0
    while i < len(args):
        if args[i] in ("-R", "--repo", "-j", "--job"):
            i += 2
            continue
        if not args[i].startswith("-"):
            return args[i]
        i += 1
    return None


_DECLARES = {"export", "declare", "typeset", "readonly", "local"}


def _apply_statement(words: list[str], carried: dict[str, str], exported: set[str],
                     hidden: set[str]) -> None:
    """What one simple command does to the release environment of the commands after
    it (rounds 9-12): `export NAME=v`, `declare -x NAME=v`, a command of plain
    assignments (exported only when the name already is), `export -n NAME`,
    `declare +x NAME`, `typeset +x NAME`, and `unset NAME`. `export NAME` without a
    value exports what only the shell knows: computed.

    Each name is EXPORTED (gh sees it), HIDDEN (known not to reach gh: unexported
    explicitly, or unset and assigned again) or neither: a plain assignment to a name
    the shell may have exported from the user's profile, which the gate cannot see."""
    if not words:
        return
    if all(_ASSIGNMENT.match(w) for w in words):
        for name, _, value in (w.partition("=") for w in words):
            if _release_env(name):
                carried[name] = value
        return
    verb, flags = words[0], [w for w in words[1:] if w.startswith(("-", "+"))]
    names = [w for w in words[1:] if not w.startswith(("-", "+"))]
    if verb == "unset":
        for name in names:
            carried.pop(name, None)
            exported.discard(name)
            hidden.add(name)  # gone; a later plain assignment is not exported
        return
    if verb not in _DECLARES:
        return
    unexport = (verb == "export" and any(f.startswith("-") and "n" in f for f in flags)) or (
        verb in ("declare", "typeset") and any(f.startswith("+") and "x" in f for f in flags))
    exports = not unexport and (verb == "export" or (
        verb in ("declare", "typeset") and any(f.startswith("-") and "x" in f for f in flags)))
    for w in names:
        name, eq, value = w.partition("=")
        if not _release_env(name):
            continue
        if eq:
            carried[name] = value
        elif name not in carried:
            carried[name] = "$" + name
        if unexport:
            exported.discard(name)
            hidden.add(name)
        elif exports:
            exported.add(name)
            hidden.discard(name)


def _command_env(carried: dict[str, str], exported: set[str], ops) -> tuple[dict[str, str], set[str]]:
    """(the release environment one command runs with, the names it sees exported):
    what the call carried, then the command's own assignments and `env -u` / `env -i`
    in order (round 11). The command's own assignments are exported to it."""
    env, seen = dict(carried), set(exported)
    for op, name, value in ops:
        if op == "clear":
            env, seen = {}, set()
        elif op == "unset":
            env.pop(name, None)
            seen.discard(name)
        elif _release_env(name):
            env[name] = value
            seen.add(name)
    return env, seen


def _computed(value: str) -> bool:
    return bool(re.search(r"[$`*?\[]", value))


def classify(cmd: str, cwd: str, branch_of=_current_branch, scope: RepoScope = GUARD_ALL,
             piped: set[str] | None = None, details: dict | None = None,
             env: dict[str, str] | None = None, also_dirs: tuple[str, ...] = ()) -> set[str]:
    """The release action classes `cmd` performs, read on the normalised view of
    each top-level simple command. When `piped` is a set, the classes whose own
    command sends its output into a pipe are added to it as well. When `details`
    is a dict, it receives what the replay needs to read the effect:
    `tags` (deploy tags pushed by their literal name), `tag_dirs` (the repository
    of each, in the same order: the same name in two repositories is two tags), `tags_literal` (every deploy tag of the call is one of `tags`),
    `merges` (the PR each merge names), `push_dirs` and `push_single` (a push to
    main with one destination), `reruns` ((run id, target dir, -R slug) of each
    `gh run rerun <id> --failed`), `sole` (the call is one simple command),
    `tag_plain` (tags pushed as a bare `<tag>` refspec, whose source is the local
    tag itself), `dispatches` (the workflow or run each dispatch names),
    `effects` (how many release effects the call can have: one per merge, push
    destination, tag, dispatch or rerun), `unbounded` (a push of every tag),
    `shape_at` (where each command starts that must be in the plain release shape:
    every release, every guarded `git push`, every guarded `gh api` write) and
    `problems` (why a release here cannot be judged at all). `env`: the release
    environment the call inherits (a shell's `-c` string inherits the shell's);
    `also_dirs`: directories a caller was in before this call's `cwd` (a shell
    started with `env -C`), each still a candidate."""
    classes: set[str] = set()
    if details is not None:
        details.setdefault("tags", [])
        details.setdefault("tag_dirs", [])
        details.setdefault("tag_remotes", [])
        details.setdefault("push_remotes", [])
        details.setdefault("tags_literal", True)
        details.setdefault("merges", [])
        details.setdefault("push_dirs", [])
        details.setdefault("push_single", True)
        details.setdefault("reruns", [])
        details.setdefault("tag_plain", [])
        details.setdefault("dispatches", [])
        details.setdefault("effects", 0)
        details.setdefault("unbounded", False)
        details.setdefault("unbounded_why", [])
        details.setdefault("shape_at", [])
        details.setdefault("problems", [])
    if not RELEASE_VERB_HINT.search(re.sub(r"[\\'\"]", "", cmd)):
        return classes
    masked = mask_inert(cmd)

    def problem(text: str) -> None:
        if details is not None and text not in details["problems"]:
            details["problems"].append(text)

    def effect(n: int = 1) -> None:
        if details is not None:
            details["effects"] += n

    # Comments and heredoc bodies are no commands; quoted words are kept for shlex.
    code = mask_inert(cmd, keep=lambda _c, region: region.kind in ("single", "double"))
    commands = simple_commands(masked)
    compound_call = False
    # Where each command can run (round 11). `cd` / `pushd` / `popd` are read from the
    # unquoted words (round 6). A `cd` that fails leaves the shell where it was, and a
    # command after `;` runs either way, so every directory the shell can be in is a
    # candidate: the directories after the chain so far succeeded (`ok`) or failed
    # (`fail`), joined per separator (`&&` runs on success, `||` on failure, anything
    # else on both). A `cd` or an assignment in a pipeline or in the background runs in
    # a subshell and moves nothing after it. The release environment (GH_REPO, GH_HOST,
    # GIT_DIR, GIT_WORK_TREE, GIT_CONFIG*, CDPATH) is carried the same way (rounds 9-12).
    views: list[tuple[int, int, View, list[str], dict[str, str], set[str], set[str]]] = []
    start_dirs = _unique([*also_dirs, cwd])
    ok_dirs, fail_dirs = start_dirs, start_dirs
    carried = dict(env or {})
    # What the call inherits is in the environment; what it sets is exported only by
    # `export` / `declare -x`, or by assigning a name that already is (round 10).
    exported = set(carried)
    hidden: set[str] = set()  # known NOT to reach the commands after (round 12)
    # `sole`: the call is ONE command besides plain `cd`s, so its exit status and
    # output are that command's own. A `cd` prints nothing a release's output could be
    # mistaken for, and when it fails the release after `&&` does not run (round 12:
    # `cd <root>; gh pr merge ...` kept its refund and follow-on).
    commands_run = 0
    before = ""
    for start, end, sep in commands:
        raw = code[start:end]
        if not raw.strip():
            continue
        dirs = ok_dirs if before == "&&" else fail_dirs if before == "||" else _unique(ok_dirs + fail_dirs)
        subshell = sep in ("|", "|&", "&") or before in ("|", "|&")
        moved: list[str] | None = None  # where a `cd` here leads from each directory
        try:
            words = shlex.split(_strip_redirects(raw), posix=True)
        except (ValueError, Ambiguous):
            words = None
            plain = re.sub(r"[\\'\"]", "", raw)
            if re.match(r"\s*(?:cd|pushd|popd)\b", plain) and not subshell:
                moved = [UNRESOLVED_DIR]
            if re.search(r"\bgit\b|\bgh\b", plain) and re.search(r"\b(?:push|merge|run|rerun|api)\b", plain):
                # Unreadable words around git/gh: judge nothing, refuse; the replay
                # charges what it might have pushed.
                problem("words that cannot be split (unbalanced quotes or a stray redirection)")
                if details is not None:
                    details["shape_at"].append(start)
                if "push" in raw:
                    classes |= {"push-main", "deploy-tag"}
        if words is None:
            commands_run += 1
        else:
            view = command_view(words)
            compound_call = compound_call or view.compound
            view_env, view_exported = _command_env(carried, exported, view.env)
            if view.kind:
                views.append((start, end, view, dirs, view_env, view_exported, hidden - view_exported))
            target = _cd_target(words)
            plain_cd = target is not _NOT_A_CD and next(
                (w for w in words if not _ASSIGNMENT.match(w)), "") == "cd"
            commands_run += 0 if plain_cd else 1
            if not subshell:
                if target is not _NOT_A_CD:
                    moved = _unique(d2 for d in dirs for d2 in _cd_dirs(d, target, view_env.get("CDPATH")))
                _apply_statement(words, carried, exported, hidden)
        run_ok, run_fail = (dirs, dirs) if moved is None else (moved, dirs)
        if before == "&&":
            ok_dirs, fail_dirs = run_ok, _unique(run_fail + fail_dirs)
        elif before == "||":
            ok_dirs, fail_dirs = _unique(run_ok + ok_dirs), run_fail
        else:
            ok_dirs, fail_dirs = run_ok, run_fail
        before = sep
    if details is not None:
        details["sole"] = commands_run == 1

    for start, end, view, dirs, view_env, view_exported, view_hidden in views:
        # Every directory the command may run in, after `env -C`; the ones it passed on
        # the way stay candidates too (round 10: one never replaces another).
        run_dirs, passed = [], []
        for d in dirs:
            for c in view.chdirs:
                passed.append(d)
                d = _chdir(d, c)
            run_dirs.append(d)
        run_dirs = _unique(run_dirs)
        # The one directory that names the effect, or none when it may be several.
        # The directories that name the effect: one, or several that Ledger.repo_of pins
        # when they are all the same repository (round 12: `cd <root>; gh pr merge`
        # from a subdirectory is one repository, not "several places").
        where_dirs = run_dirs[0] if len(run_dirs) == 1 else tuple(run_dirs)
        # A computed GH_HOST may be any host: guarded whatever the repository says.
        host_unknown = _computed(view_env.get("GH_HOST", ""))
        # GIT_DIR / GIT_WORK_TREE point git, and gh through git, at a repository named
        # only when it runs, and GIT_CONFIG* can rewrite the remotes gh reads
        # (`url.<base>.insteadOf`) (rounds 6 and 11).
        git_elsewhere = bool(_GIT_REPO_ENV & view_env.keys()) or any(
            name.startswith("GIT_CONFIG") for name in view_env)

        def guarded(where, *slugs: str | None, remotes=()) -> bool:
            return host_unknown or scope.guards(where, *slugs, also_dirs=(*passed, *dirs), remotes=remotes)

        def judged() -> None:
            if details is not None:
                details["shape_at"].append(start)

        def add(cls: str) -> None:
            classes.add(cls)
            if piped is not None and _stops_at_pipe(masked, start):
                piped.add(cls)

        if view.kind == "shell":
            # Round-6 review: `bash -lc 'gh pr merge 42 --squash'` released unseen. The
            # string (or a heredoc on the shell's standard input) is read as a call of
            # its own; anything in it that releases, or that the plain shape would
            # refuse, is refused here: a release command runs as plain words.
            texts = [view.payload] if view.payload is not None else [
                cmd[r.start:r.end] for r in inert_regions(cmd)
                if r.kind == "heredoc" and start <= r.anchor < end]
            for text in texts:
                inner: dict = {}
                inner_classes = classify(text, run_dirs[0], branch_of, scope, None, inner, env=view_env,
                                         also_dirs=tuple(_unique([*passed, *run_dirs])))
                inner_problem = release_shape_problem(text, inner["shape_at"]) or next(
                    iter(inner["problems"]), None)
                if not (inner_classes or inner_problem):
                    continue
                how = (f"the command string handed to `{view.args[0]} -c`" if view.payload is not None
                       else f"the heredoc `{view.args[0]}` reads on its standard input")
                problem(f"a release command inside {how}"
                        + (f" by `{view.runner}`" if view.runner else "")
                        + "; run the release command itself, not through a shell")
                classes.update(inner_classes)
                if details is not None:
                    _merge_details(details, inner)
            continue

        if view.kind == "git-push":
            git_dirs = ([None] if git_elsewhere else []) + list(view.git_dirs)
            repo_dirs, walked = [], []
            for d in run_dirs:
                for g in git_dirs:
                    walked.append(d)
                    d = _chdir(d, g)
                repo_dirs.append(d)
            repo_dirs = _unique(repo_dirs)
            dest = _push_destination(view.args)
            # Round 11: where the push goes is a candidate too: a URL, or the checkout's
            # remote of that name (its push URL); one that cannot be resolved is guarded.
            remotes = [(d, dest) for d in repo_dirs] if dest else ()
            if _help_only(view.args) or not (host_unknown or scope.guards(
                    repo_dirs, also_dirs=(*walked, *passed, *dirs), remotes=remotes)):
                continue
            judged()  # every push here: a ref through `$X` could be main
            if view.problem:
                problem(view.problem)
            if any(name.startswith("GIT_CONFIG") for name in view_env):
                problem("git configuration set through a GIT_CONFIG* variable in this call, which "
                        "can change what a push updates")
            if compound_call:
                problem("a release command inside a compound command (only a top-level simple "
                        "command may release)")
            if bad := _push_option_problem(view.args):
                problem(bad)
            if len([t for t in view.args if not t.startswith("-")]) < 2:
                # Round-6 review: without a refspec git pushes what its configuration
                # says (push.default, remote.<name>.push, the upstream), so the
                # destination is not known here.
                problem("no explicit refspec (a bare `git push` or `git push origin` pushes what git's "
                        "configuration says; name the branch: `git push origin <branch>`)")
            repo_dir = repo_dirs[0] if len(repo_dirs) == 1 else tuple(repo_dirs)
            before = set(classes)
            _classify_push(view.args, repo_dir, dest, branch_of, classes, details, add, effect, cmd)
            if view.via_app and classes - before:
                problem(f"a release push run through {APP_WRAPPER}, which pushes only a feature branch")
            continue

        # The repository gh acts on: its -R or a URL, else GH_REPO when gh sees it
        # (exported, or set for the command), else the checkout's remotes (through
        # GIT_DIR / GIT_WORK_TREE when set). Every one of them is a scope candidate;
        # the effect is pinned to one only when the command fixes it (round 11).
        visible_repo = view_env.get("GH_REPO") if "GH_REPO" in view_exported else None
        # Set, and neither known to reach gh nor known not to (round 12: `export -n`).
        maybe_repo = bool(view_env.get("GH_REPO")) and not visible_repo and "GH_REPO" not in view_hidden
        repo_candidate = None if "GH_REPO" in view_hidden else view_env.get("GH_REPO")
        gh_dirs = run_dirs + ([UNRESOLVED_DIR] if git_elsewhere else [])

        def record(pinned: str | None) -> tuple[str, str | None]:
            """(directory, repository) to read the effect by. Unpinned when the command
            does not fix its repository and the directory may be several, git is
            pointed elsewhere, or a GH_REPO may or may not reach gh: an unresolved
            directory, so no lookup, identity, refund or credit is read for it."""
            if pinned:
                return where_dirs, pinned
            if maybe_repo or git_elsewhere:
                return UNRESOLVED_DIR, None
            return where_dirs, None

        if _help_only(view.args):
            continue
        if view.kind == "gh-api":
            method, endpoint, fields, _has_input = _api_request(view.args)
            api_slug = re.search(r"repos/([^/\s]+/[^/\s]+)/", endpoint or "")
            explicit = api_slug.group(1) if api_slug and ":owner" not in api_slug.group(1) \
                and "{owner}" not in api_slug.group(1) else None
            if not guarded(gh_dirs, explicit, view.slug, repo_candidate):
                continue
            if method == "GET":
                continue
            judged()
            found = _api_effect(endpoint or "", fields) if endpoint else ("deploy-tag", None)
            if not found:
                continue
            cls, named = found
            if view.problem:
                problem(view.problem)
            if compound_call:
                problem("a release command inside a compound command (only a top-level simple "
                        "command may release)")
            add(cls)
            effect()
            if details is None:
                continue
            # The repository a `repos/:owner/:repo/...` path reaches is gh's -R / GH_REPO.
            where, repo = record(explicit or view.slug or visible_repo)
            if cls == "merge":
                details["merges"].append({"ref": named, "dir": where, "slug": repo, "auto": False,
                                          "head": None})
            elif cls == "dispatch":
                details["dispatches"].append({"workflow": named, "run": None, "slug": repo, "dir": where})
            elif cls == "rerun":
                details["reruns"].append((named, where, repo))
            elif cls == "deploy-tag":
                details["tags_literal"] = False
            continue

        if view.kind == "gh-pr-merge":
            ref, slug, auto = _merge_target(view.args)
            selector = ref
        elif view.kind == "gh-workflow-run":
            ref, slug = _workflow_target(view.args)
            selector = None
        else:
            one = _rerun_of_one_run(view.args)
            slug = one[1] if one else next((view.args[k + 1] for k, t in enumerate(view.args[:-1])
                                            if t in ("-R", "--repo")), None)
            selector = _rerun_selector(view.args)
        # Round-6 review: a PR or run URL names its own repository, and gh acts on it
        # whatever the cwd says. Round 10: every repository the command names (the
        # subcommand's -R, gh's global -R, GH_REPO however it was set, the URL) is a
        # candidate beside the cwd, and any guarded one guards the command.
        url_slug = slug_of_selector(selector)
        if not guarded(gh_dirs, slug, view.slug, repo_candidate, url_slug):
            continue
        flag = slug or view.slug or repo_candidate
        if url_slug and flag and _norm_slug(flag) != url_slug:
            # A URL and a -R / GH_REPO that name different repositories leave the
            # target to gh.
            problem(f"a PR or run URL and a repository flag that disagree (`{url_slug}` and `{flag}`)")
        where, repo = record(url_slug or slug or view.slug or visible_repo)
        judged()
        if view.problem:
            problem(view.problem)
        if compound_call:
            problem("a release command inside a compound command (only a top-level simple "
                    "command may release)")
        effect()
        if view.kind == "gh-pr-merge":
            add("merge")
            if details is not None:
                details["merges"].append({"ref": ref, "dir": where, "slug": repo, "auto": auto,
                                          "head": _match_head(view.args)})
        elif view.kind == "gh-workflow-run":
            add("dispatch")
            if details is not None:
                details["dispatches"].append({"workflow": ref, "run": None, "slug": repo, "dir": where})
        elif one:
            add("rerun")
            if details is not None:
                details["reruns"].append((one[0], where, repo))  # gh's -R / GH_REPO / URL too
        else:
            add("dispatch")
            if details is not None:
                run_id = next((t for t in view.args if re.fullmatch(r"\d+", t)), None)
                details["dispatches"].append({"workflow": None, "run": run_id, "slug": repo, "dir": where})
    return classes


def _push_destination(args: list[str]) -> str | None:
    """The repository argument of `git push <args>`: a remote name or a URL."""
    skip = False
    for tok in args:
        if skip:
            skip = False
        elif tok.startswith("-"):
            skip = tok in _PUSH_FLAGS_WITH_VALUE
        else:
            return tok
    return None


def _classify_push(args: list[str], repo_dir: str, remote: str | None, branch_of, classes: set[str],
                   details: dict | None, add, effect, cmd: str) -> None:
    """The release classes of `git push <args>` run in `repo_dir` to `remote`."""
    positional: list[str] = []
    skip = False
    flags: set[str] = set()
    for tok in args:
        if skip:
            skip = False
            continue
        if tok.startswith("-"):
            flags.add(tok.split("=", 1)[0])
            if tok in _PUSH_FLAGS_WITH_VALUE:
                skip = True
            continue
        positional.append(tok)
    refspecs = positional[1:]
    mine: set[str] = set()  # what THIS push does
    deleting = bool(flags & {"--delete", "-d"})
    # A wildcard refspec (`refs/heads/*:refs/heads/*`) can move main and push tags;
    # what it matches is decided when it runs.
    for r in refspecs:
        if "*" in r:
            dst = r.lstrip("+").split(":")[-1]
            if not dst.startswith("refs/tags/"):
                mine.add("push-main")
            if not dst.startswith("refs/heads/"):
                mine.add("deploy-tag")
    if flags & {"--mirror"}:
        mine.add("deploy-tag")  # every ref, tags included

    if flags & {"--tags", "--follow-tags"} or any("deploy-" in r or "refs/tags/" in r for r in refspecs):
        mine.add("deploy-tag")
        effect(len([r for r in refspecs if "deploy-" in r or "refs/tags/" in r]))
        if details is not None:
            if flags & {"--tags", "--follow-tags"} or deleting:
                details["tags_literal"] = False
            for r in refspecs:
                name = r.lstrip("+").split(":")[-1].removeprefix("refs/tags/")
                if name.startswith("deploy-") and _literal(name) and not r.startswith(":") and not deleting:
                    details["tags"].append(name)
                    details["tag_dirs"].append(repo_dir)
                    details["tag_remotes"].append(remote)
                    if r in (name, f"refs/tags/{name}"):
                        details["tag_plain"].append(name)
                elif "deploy-" in r or "refs/tags/" in r:
                    details["tags_literal"] = False
    elif any("$" in r for r in refspecs) and _DEPLOY_TAG_ASSIGN.search(cmd):
        mine.add("deploy-tag")
        effect()
        if details is not None:
            details["tags_literal"] = False

    if flags & {"--all", "--mirror"}:
        mine.add("push-main")
    else:
        current_branch_needed = not refspecs and not (flags & {"--tags", "--follow-tags"})
        for ref in refspecs:
            dst = ref.lstrip("+").split(":")[-1]
            dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
            if dst in _MAIN_BRANCHES:
                mine.add("push-main")
            elif dst == "HEAD" or ("$" in dst and "deploy-tag" not in classes | mine):
                current_branch_needed = True
        # A repository named only when the command runs may be on main.
        if current_branch_needed and any(_unresolved(d) or branch_of(d) in _MAIN_BRANCHES
                                         for d in _dirs_of(repo_dir)):
            mine.add("push-main")
    if "push-main" in mine:
        effect()
    if "push-main" in mine and details is not None:
        details["push_dirs"].append(repo_dir)
        details["push_remotes"].append(remote)
        if flags & {"--all", "--mirror"} or len(refspecs) > 1:
            details["push_single"] = False
    if mine and details is not None:
        # A push that can update more than one release ref is refused whole: one
        # approval is one effect, and these leave the count to git.
        why = sorted(f for f in flags & {"--tags", "--follow-tags", "--mirror", "--all"})
        if any("*" in r for r in refspecs):
            why.append("a wildcard refspec")
        if len(refspecs) > 1:
            why.append(f"{len(refspecs)} destination refs")
        if why:
            details["unbounded"] = True
            details["unbounded_why"] += why
    for cls in mine:
        add(cls)


# --------------------------------------------------------------------------
# What a release command did: facts read from GitHub and git.
# --------------------------------------------------------------------------

# One GitHub REST round trip (measured 0.53 s from this machine, 2026-10-12). The
# hook's own budget is 30 s (hooks.json); a hook killed at that bound is a
# non-blocking error and the command would run unjudged. So all lookups of one
# decision share LOOKUP_BUDGET_S: 30 s minus the 1 s transcript-lag pause, minus
# ~9 s for interpreter start, two transcript reads and the local git calls. A
# lookup that would run past it is not made, and its answer is "unknown", which
# charges the approval: a refusal, never a release. Each single lookup stops at
# GH_API_TIMEOUT_S so one slow call cannot eat the whole budget.
GH_API_TIMEOUT_S = 10
LOOKUP_BUDGET_S = 20

# A dispatch or rerun that reported failure is refunded only on PROOF that GitHub
# made no run of it. GitHub registers a run 3-22 s after the event (measured over
# 45 tag pushes; release-run.py `find` stops at 60 s), so until RUN_REGISTER_S
# after the attempt an empty list proves nothing and the attempt stays charged;
# a later decision looks again. The attempt's time is the transcript's timestamp
# of the tool call, written before the command ran, on this machine's clock; a run
# GitHub created up to CLOCK_MARGIN_S before it still counts as possibly this
# one. Reasoned, not measured: NTP keeps the two clocks well under a second apart,
# and a wider margin only charges more.
RUN_REGISTER_S = 60
CLOCK_MARGIN_S = 60
# `gh run list` answers newest first, so the first run older than the window ends
# the question; 20 rows hold every dispatch of one workflow in a few minutes.
RUN_LIST_LIMIT = 20


def _epoch(stamp) -> float | None:
    """Seconds since the epoch of an ISO-8601 timestamp (`2026-10-20T12:00:00.000Z`)."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class PrState(NamedTuple):
    state: str
    merge_sha: str | None
    url: str | None


class Produced(NamedTuple):
    """A commit a release of this session put on main, which its approval's
    follow-on may deploy. `checked`: read from GitHub (a merge commit); otherwise
    read from the push's own output and trusted only while it is on origin's main."""
    sha: str
    repo_dir: str
    checked: bool
    repo: str  # Ledger.repo_of: the follow-on deploys only in this repository


def _repo_path(repo: str | None) -> str | None:
    """`owner/repo` for a GitHub path or `-R`, from the ONE normaliser (round 11:
    `repos/github.com/EndoxiaBV/Endoxia/...` answered nothing). None when it is not
    a plain owner/repo: the lookup is not made, and its answer is "unknown"."""
    if not repo or _computed(repo):
        return None
    slug = _norm_slug(repo)
    return slug if re.fullmatch(r"[\w.-]+/[\w.-]+", slug) else None


class World:
    """The lookups that tell whether a release took effect. None is "could not
    be read"; the ledger charges that. Answers are memoised for the decision.

    Every GitHub lookup takes the repository Ledger.repo_of resolved, and names it
    explicitly (`-R owner/repo`, `repos/owner/repo/...`), never gh's `{owner}/{repo}`
    placeholder or the cwd: those follow GH_REPO and the checkout's remotes, which may
    be another repository (round 11). Every lookup runs without the release
    environment (`_lookup_env`)."""

    def __init__(self, run=subprocess.run, clock=time.monotonic, budget_s: float = LOOKUP_BUDGET_S,
                 wall=time.time, app_config=None, load_app_config=agent_app.load_config):
        self._run = run
        # The agent App's config, read only when a review could approve something:
        # None when this machine has not opted in, or when its config is unusable
        # (`app_config_problem` says why; a broken config approves nothing).
        self._app_config = app_config
        self._load_app_config = load_app_config if app_config is None else None
        self.app_config_problem: str | None = None
        self._clock = clock
        self._wall = wall
        self._deadline = clock() + budget_s
        self._memo: dict[tuple, object] = {}

    def _call(self, argv: list[str], cwd: str | None = None) -> tuple[int, str] | None:
        key = (tuple(argv), cwd)
        if key in self._memo:
            return self._memo[key]  # type: ignore[return-value]
        left = self._deadline - self._clock()
        if left <= 0:
            return None
        try:
            out = self._run(argv, cwd=cwd, capture_output=True, text=True,
                            timeout=min(GH_API_TIMEOUT_S, left), env=_lookup_env())
            answer: tuple[int, str] | None = (out.returncode, out.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            answer = None
        self._memo[key] = answer
        return answer

    def _git(self, argv: list[str]) -> str | None:
        out = self._call(argv)
        return out[1] if out and out[0] == 0 else None

    def repo_slug(self, directory: str) -> str | None:
        """THE repository gh acts on in a checkout (`checkout_repositories`), None
        when it cannot be told: the effect is then unpinned."""
        return checkout_repositories(directory, self._git)[1]

    def remote_slug(self, directory: str, remote: str | None) -> str | None:
        """The repository a push destination names (shared with the scope)."""
        return remote_slug(directory, remote, self._git)

    def pr_state(self, ref: str, repo: str | None) -> PrState | None:
        path = _repo_path(repo)
        if path is None:
            return None
        out = self._call(["gh", "pr", "view", ref, "--json", "state,mergeCommit,url", "-R", path])
        if out is None or out[0] != 0:
            return None
        try:
            data = json.loads(out[1])
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("state"):
            return None
        return PrState(str(data["state"]).upper(), (data.get("mergeCommit") or {}).get("oid"), data.get("url"))

    @property
    def app_config(self) -> "agent_app.AppConfig | None":
        if self._load_app_config is not None:
            loader, self._load_app_config = self._load_app_config, None
            try:
                self._app_config = loader()
            except agent_app.ConfigError as error:
                self.app_config_problem = str(error)
        return self._app_config

    @app_config.setter
    def app_config(self, value) -> None:
        self._app_config, self._load_app_config = value, None

    def current_branch(self, directory: str) -> str | None:
        return _current_branch(directory)

    def review(self, ref: str, repo: str | None) -> "agent_app.Review | None":
        """What the owner's review of PR `ref` in `repo` says now (agent_app.judge_review);
        None when no App covers `repo` or GitHub cannot say."""
        config, path = self.app_config, _repo_path(repo)
        if config is None or path is None or not config.covers(path) or not re.fullmatch(r"\d+", ref or ""):
            return None
        pr = self._json_lines(["gh", "api", f"repos/{path}/pulls/{ref}", "--jq",
                               "{user: {login: .user.login}, head: {sha: .head.sha}, state: .state}"])
        reviews = self._json_lines(["gh", "api", "--paginate", f"repos/{path}/pulls/{ref}/reviews", "--jq",
                                    ".[] | {user: {login: .user.login}, state: .state, commit_id: .commit_id}"])
        if not pr or reviews is None:
            return None
        return agent_app.judge_review(pr[0], reviews, config)

    def branch_pr(self, branch: str | None, repo: str | None) -> str | None:
        """The one open PR whose head is `branch` of `repo`, None when there is not exactly one."""
        path = _repo_path(repo)
        if path is None or not branch or not re.fullmatch(r"[\w./-]+", branch):
            return None
        rows = self._json_lines(["gh", "api", f"repos/{path}/pulls?head={path.split('/')[0]}:{branch}&state=open",
                                 "--jq", ".[].number"])
        return str(rows[0]) if rows and len(rows) == 1 else None

    def _json_lines(self, argv: list[str]) -> list | None:
        out = self._call(argv)
        if out is None or out[0] != 0:
            return None
        try:
            return [json.loads(line) for line in out[1].splitlines() if line.strip()]
        except ValueError:
            return None

    def remote_tag(self, tag: str, repo_dir: str, remote: str | None = "origin") -> str | None:
        """The commit the tag names where it was pushed (`remote`: a remote of the
        checkout or a URL), "" when it has no such tag."""
        if not remote or not repo_dir or _unresolved(repo_dir) or not os.path.isdir(repo_dir):
            return None
        out = self._call(["git", "-C", repo_dir, "ls-remote", "--exit-code", remote,
                          f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"])
        if out is None:
            return None
        if out[0] == 2:
            return ""
        if out[0] != 0:
            return None
        refs = {ref.strip(): sha.strip() for sha, ref in
                (line.split("\t", 1) for line in out[1].splitlines() if "\t" in line)}
        # An annotated tag is listed twice; the peeled `^{}` line names its commit.
        return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}") or None

    def local_tag(self, tag: str, repo_dir: str) -> str | None:
        if not repo_dir or _unresolved(repo_dir) or not os.path.isdir(repo_dir):
            return None
        out = self._call(["git", "-C", repo_dir, "rev-parse", "--verify", "--quiet",
                          f"refs/tags/{tag}^{{commit}}"])
        if out is None or out[0] != 0:
            return None
        return out[1].strip() or None

    def on_main(self, sha: str, repo: str | None) -> bool | None:
        """Whether `sha` is `repo`'s main or an ancestor of it, as GitHub says."""
        path = _repo_path(repo)
        if path is None:
            return None
        out = self._call(["gh", "api", f"repos/{path}/compare/{sha}...main", "--jq", ".status"])
        if out is None or out[0] != 0:
            return None
        status = out[1].strip()
        return True if status in ("identical", "ahead") else False if status else None

    def now(self) -> float:
        return self._wall()

    def workflow_runs_since(self, workflow: str, repo: str | None, since: float) -> bool | None:
        """Whether GitHub has a run of `workflow` in `repo` created at or after `since`."""
        path = _repo_path(repo)
        if path is None:
            return None
        out = self._call(["gh", "run", "list", "--workflow", workflow, "--limit", str(RUN_LIST_LIMIT),
                          "--json", "createdAt", "-R", path])
        if out is None or out[0] != 0:
            return None
        try:
            rows = json.loads(out[1])
        except ValueError:
            return None
        times = [_epoch(r.get("createdAt")) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [None]
        if any(t is None for t in times):
            return None
        return any(t >= since for t in times)

    def run_started(self, run_id: str, repo: str | None) -> float | None:
        """When the run's newest attempt started (`run_started_at`; a rerun moves it)."""
        path = _repo_path(repo)
        if path is None:
            return None
        out = self._call(["gh", "api", f"repos/{path}/actions/runs/{run_id}", "--jq", ".run_started_at"])
        if out is None or out[0] != 0:
            return None
        return _epoch(out[1].strip())

    def run_info(self, run_id: str, repo: str | None) -> dict | None:
        """The run's trigger as GitHub recorded it: {"event", "head_branch"}. For a
        tag push, `head_branch` is the tag. None when it cannot be read."""
        path = _repo_path(repo)
        if path is None:
            return None
        out = self._call(["gh", "api", f"repos/{path}/actions/runs/{run_id}", "--jq",
                          "{event: .event, head_branch: .head_branch}"])
        if out is None or out[0] != 0:
            return None
        try:
            return json.loads(out[1])
        except ValueError:
            return None


# --------------------------------------------------------------------------
# What the user typed.
# --------------------------------------------------------------------------

_ENVELOPE_TAG = re.compile(r"<(command-name|command-message|command-args)>(.*?)</\1>", re.S)
_APPROVE_LINE = re.compile(r"/(?:[\w-]+:)?approve(?:[ \t]+(.*))?")


def parse_typed_command(content) -> tuple[str, str] | None:
    """(name, args) when the WHOLE content is a slash-command envelope."""
    if not isinstance(content, str):
        return None
    if _ENVELOPE_TAG.sub("", content).strip():
        return None
    tags = _ENVELOPE_TAG.findall(content)
    names = [v.strip() for k, v in tags if k == "command-name"]
    if len(names) != 1 or not names[0].startswith("/"):
        return None
    args = next((v for k, v in tags if k == "command-args"), "")
    return names[0][1:], args.strip()


def approval_names(args: str) -> list[str]:
    return [bare(t.rstrip(".!;:")) for t in re.split(r"[,\s]+", args) if bare(t.rstrip(".!;:"))]


def _approve_lines(lines: list[str]) -> list[str] | None:
    """The args of each line when EVERY line is `/approve <names>`, else None."""
    out = []
    for line in lines:
        m = _APPROVE_LINE.fullmatch(line.strip())
        if not m:
            return None
        out.append((m.group(1) or "").strip())
    return out


def typed_commands(content) -> list[tuple[str, str]]:
    """The commands a user prompt carries, as (name, args). Owner decision
    2026-10-20, after review: an approval counts only when the WHOLE message is
    the approval. That is a slash command Claude Code ran (its envelope; for
    `/approve`, every further line of its args must be an `/approve` line too), or
    a message whose every non-empty line is `/approve <names>` (spaces around it
    allowed). An `/approve` line inside prose, a T3 handoff or any other text is
    no approval: in T3 an injected message (a thread send, a delegate notice) is
    recorded exactly like a typed one, so text cannot prove who wrote it."""
    if not isinstance(content, str):
        return []
    envelope = parse_typed_command(content)
    if envelope:
        name, args = envelope
        if bare(name) != "approve":
            return [(name, args)]
        lines = [line for line in args.splitlines() if line.strip()]
        rest = _approve_lines(lines[1:])
        if rest is None:
            return []  # prose after the command: the message is not the approval
        return [("approve", lines[0].strip() if lines else "")] + [("approve", a) for a in rest]
    lines = [line for line in content.splitlines() if line.strip()]
    approvals = _approve_lines(lines) if lines else None
    return [("approve", a) for a in approvals] if approvals is not None else []


def _is_human_prompt(entry: dict) -> bool:
    return (
        entry.get("type") == "user"
        and not entry.get("isMeta")
        and not entry.get("isSidechain")
        and not entry.get("isCompactSummary")
    )


# --------------------------------------------------------------------------
# How a tool call ended.
# --------------------------------------------------------------------------

# The first words of a result for a call that NEVER RAN (measured over 150
# transcripts, 2026-10-20): a PreToolUse hook's refusal, Claude Code's own
# refusals, the user rejecting the call. A command that ran and failed starts
# with "Exit code N". Anything else that is an error (a timeout, an interrupt)
# may have run, and is read as "stopped".
_REFUSED_PREFIXES = ("PreToolUse:", "<tool_use_error>", "This session is isolated",
                     "The user doesn't want to", "Permission to use")
_EXIT_CODE = re.compile(r"^Exit code (\d+)")


def result_outcome(block: dict) -> str:
    """refused | failed | stopped | ok, for a tool_result block."""
    content = _result_text(block)
    if block.get("is_error") is not True:
        return "ok"
    head = content.lstrip()
    if head.startswith(_REFUSED_PREFIXES):
        return "refused"
    if _EXIT_CODE.match(head):
        return "failed"
    return "stopped"


def parse_task_notification(content: str) -> tuple[str, str, int | None] | None:
    """(tool-use id, status, exit code) of a background task's completion notice,
    the `<task-notification>` entry Claude Code writes when the task ends. The id and
    the status are the FIRST of their kind: Claude Code writes both before the
    summary, and the summary quotes the command's description, which the model
    wrote. The exit code is the LAST one in the summary, for the same reason:
    Claude Code appends it after that description."""
    if not content.startswith("<task-notification>"):
        return None
    tid = re.search(r"<tool-use-id>([^<]+)</tool-use-id>", content)
    status = re.search(r"<status>([^<]+)</status>", content)
    if not tid or not status:
        return None
    start, end = content.find("<summary>", status.end()), content.rfind("</summary>")
    codes = re.findall(r"exit code (\d+)", content[start:end]) if 0 <= start < end else []
    return tid.group(1).strip(), status.group(1).strip(), (int(codes[-1]) if codes else None)


def notice_outcome(status: str, exit_code: int | None) -> str:
    """How a background command ended: `failed` or a non-zero exit is a failure;
    `completed` is success; killed, stopped or unknown ended without a verdict."""
    if status == "failed":
        return "failed"
    if status == "completed":
        return "failed" if exit_code not in (None, 0) else "ok"
    return "stopped"


_PUSHED_MAIN = re.compile(r"^\s*[+ ]?\s*[0-9a-f]{7,40}\.\.\.?([0-9a-f]{7,40})\s+\S+\s+->\s+"
                          r"(?:refs/heads/)?(?:main|master)\b", re.M)


# git's own report that it did NOT move main: a rejected update, or a source that
# matches nothing. In a lone push the output is git's; a `remote:` line (the
# server's words) does not match. A network failure prints neither: unknown.
_PUSH_REFUSED = re.compile(r"^\s*! \[(?:rejected|remote rejected)\]\s+\S+\s+->\s+(?:refs/heads/)?"
                           r"(?:main|master)\b|^error: src refspec \S+ does not match any", re.M)


def _push_refused(output: str) -> bool:
    return bool(_PUSH_REFUSED.search(output or ""))


def _pushed_main_commit(output: str) -> str | None:
    """The commit `git push` reports it put on main (`a1b2c3d..e4f5a6b  main -> main`)."""
    hits = _PUSHED_MAIN.findall(output or "")
    return hits[-1] if len(set(hits)) == 1 else None


# --------------------------------------------------------------------------
# The ledger: what the user approved, and which effects used it.
# --------------------------------------------------------------------------

class Authorization:
    """One approval: a typed release skill, or one name of a typed /approve. It
    covers one effect of each action its skill declares, and for a skill with a
    follow-on, one more effect of that action on a commit its own release produced."""

    def __init__(self, skill: str, actions: tuple[str, ...], follow_on: tuple[str, ...]):
        self.skill = skill
        self.left = Counter({a: 1 for a in actions})
        self.follow_left = Counter({a: 1 for a in follow_on})
        self.produced: list[Produced] = []

    def open(self) -> bool:
        return any(v > 0 for v in self.left.values())

    def armed(self, action: str) -> bool:
        return self.follow_left[action] > 0 and bool(self.produced)


def _same_commit(produced: Produced, sha: str) -> bool:
    return len(produced.sha) >= 7 and sha.startswith(produced.sha)


# The name a merge approved by the owner's review on GitHub carries (agent_app.py).
REVIEW_APPROVAL = "github-review"


class Ledger:
    def __init__(self, actions_of=lambda _name: (), follow_on_of=lambda _name: (),
                 world: World | None = None):
        self.auths: list[Authorization] = []
        self.active: str | None = None  # the release skill this session runs now
        self.last_approve_args: list[str] = []
        self._actions_of = actions_of
        self._follow_on_of = follow_on_of
        self.world = world or World()
        # Round-10 review: every key names its repository (`repo_of`), so one tag
        # name, PR number or run id in two repositories is two effects / credits.
        self._effects: set[tuple[str, str, str]] = set()  # (action, repo, identity)
        # Runs a release of THIS session started, each re-runnable once with
        # `gh run rerun <id> --failed` under the approval that released it:
        # ("tags", repo, frozenset of deploy tags pushed) or ("run", repo, id a dispatch printed).
        self.run_credits: list[tuple[str, str, object]] = []
        self.reruns_used: set[tuple[str, str]] = set()  # (repo, run id)

    def repo_of(self, directory: str | None, slug: str | None = None, remote: str | None = None) -> str | None:
        """THE repository of an effect or credit, through the one normaliser: the
        owner/repo the command named (-R, a URL, an API path), else the push
        destination (a URL, or the checkout's remote), else the checkout's one GitHub
        remote. None when it cannot be pinned (computed, unresolved, several remotes):
        such an effect is charged with no lookup, identity, refund or credit (round 11).
        Every lookup is made with what this returns."""
        if slug:
            return None if _computed(slug) else _repo_path(slug)
        dirs = _dirs_of(directory)
        if remote:
            repos = {self.world.remote_slug(d, remote) for d in dirs or ("",)}
        else:
            # Every directory it may have run in, resolved to its repository: pinned only
            # when they all name the same one (round 12).
            repos = {None if _unresolved(d) else self.world.repo_slug(d) for d in dirs} or {None}
        return next(iter(repos)) if len(repos) == 1 else None

    # -- what the user typed ------------------------------------------------

    def typed(self, name: str, args: str) -> None:
        b = bare(name)
        if b == "approve":
            names = approval_names(args)
            self.last_approve_args = names
            # Every word must name a release skill: "/approve ship and deploy later"
            # is a sentence, and approves nothing (`--status` names the stray words).
            if names and all(self._actions_of(n) for n in names):
                for n in names:
                    self.auths.append(Authorization(n, self._actions_of(n), self._follow_on_of(n)))
            return
        actions = self._actions_of(name)
        if not actions:
            return
        self.active = b
        # The user running the skill continues its open approval (the typed run
        # uses it); otherwise the typed line is the approval of this run.
        if not any(a.skill == b and a.open() for a in self.auths):
            self.auths.append(Authorization(b, actions, self._follow_on_of(name)))

    def skill_invoked(self, name: str) -> None:
        if self._actions_of(name):
            self.active = bare(name)

    # -- what covers an action ---------------------------------------------

    def open_for(self, skill: str) -> bool:
        return any(a.skill == bare(skill) and a.open() for a in self.auths)

    def armed(self, action: str) -> list[Authorization]:
        return [a for a in self.auths if a.armed(action)]

    def follow_on_covering(self, action: str, sha: str | None, repo: str | None) -> Authorization | None:
        """The approval whose follow-on covers `action` on commit `sha` in `repo`: one
        of the commits its own release produced there, a pushed one only while it is
        on main."""
        if not sha:
            return None
        for a in self.armed(action):
            for p in a.produced:
                if repo and _same_commit(p, sha) and p.repo == repo and (
                        p.checked or self.world.on_main(sha, p.repo) is True):
                    return a
        return None

    def covers(self, action: str, sha: str | None = None, repo: str | None = None) -> bool:
        if any(a.left[action] > 0 for a in self.auths):
            return True
        return self.follow_on_covering(action, sha, repo) is not None

    def skill_allowed(self, name: str) -> bool:
        """An open approval of the skill, or (deploy after /ship) every action of the
        skill covered by an armed follow-on."""
        if self.open_for(name):
            return True
        actions = self._actions_of(name)
        return bool(actions) and all(self.armed(a) for a in actions)

    # -- effects ------------------------------------------------------------

    def charge(self, action: str, identity: str | None = None, sha: str | None = None,
               produced: Produced | None = None, repo: str | None = None) -> None:
        """Use one approval for one effect. The same effect (a PR merged, a tag
        pushed, in the same repository `repo`) is charged once however many commands
        attempted it."""
        if identity is not None and repo is not None:
            if (action, repo, identity) in self._effects:
                return
            self._effects.add((action, repo, identity))
        if sha:
            for a in self.auths:
                if repo and a.follow_left[action] > 0 and any(_same_commit(p, sha) and p.repo == repo
                                                              for p in a.produced):
                    a.follow_left[action] -= 1
                    return
        candidates = [a for a in self.auths if a.left[action] > 0]
        if candidates:
            mine = [a for a in candidates if a.skill == self.active]
            chosen = (mine or candidates)[0]
            chosen.left[action] -= 1
            if produced and chosen.follow_left:
                chosen.produced.append(produced)
            return
        # An effect on a commit the gate could not read, or no other approval left:
        # spend a follow-on too. Never fewer charges than effects.
        armed = self.armed(action)
        if armed:
            armed[0].follow_left[action] -= 1

    def _merge_effect(self, details: dict, outcome: str) -> None:
        refs = details.get("merges") or []
        repos = [self.repo_of(r["dir"], r["slug"]) for r in refs]
        # An unpinned merge is never looked up: a PR of that number in the wrong
        # repository could be MERGED and dedupe this one away (round 11).
        states = [self.world.pr_state(r["ref"], repo) if repo and _literal(r["ref"]) else None
                  for r, repo in zip(refs, repos)]
        merged = [(s, r, repo) for s, r, repo in zip(states, refs, repos) if s and s.state == "MERGED"]
        for s, r, repo in merged:
            identity = s.url or s.merge_sha or str(r["ref"])
            if (repo and ("merge", repo, identity) not in self._effects
                    and not any(a.left["merge"] > 0 for a in self.auths)):
                # No typed approval paid for it: the owner's review on GitHub did, when
                # it approves the head that was merged. That approval then covers this
                # one merge and the one staging tag on its merge commit, as /ship's does.
                review = self.world.review(str(r["ref"]), repo)
                if review is not None and review.approved:
                    self.auths.append(Authorization(REVIEW_APPROVAL, ("merge",), self._follow_on_of("ship")))
            self.charge("merge", identity=identity, repo=repo,
                        produced=Produced(s.merge_sha, _first_dir(r["dir"]), True, repo) if s.merge_sha else None)
        if merged:
            return
        nothing_happened = (
            outcome in ("failed", "stopped") and refs
            and all(_literal(r["ref"]) and not r["auto"] for r in refs)
            and all(s is not None for s in states)
        )
        if not nothing_happened:
            self.charge("merge")

    def _tag_effect(self, details: dict, outcome: str) -> None:
        tags = details.get("tags") or []
        dirs = details.get("tag_dirs") or []
        remotes = details.get("tag_remotes") or ["origin"] * len(tags)
        # Each tag in the repository it was pushed to (round 11: the destination).
        pushed = [(t, d, r, self.repo_of(d, None, r)) for t, d, r in zip(tags, dirs, remotes)]
        pinned = [(t, d, r, repo) for t, d, r, repo in pushed if repo]
        found = [(t, repo, self.world.remote_tag(t, _first_dir(d), r)) for t, d, r, repo in pinned]
        present = [(t, repo, sha) for t, repo, sha in found if sha]
        for t, repo, sha in present:
            self.charge("deploy-tag", identity=t, sha=sha, repo=repo)
        for _unpinned in range(len(pushed) - len(pinned)):
            self.charge("deploy-tag")  # where it went cannot be read: charged, never refunded
        if not present and len(pinned) == len(pushed):
            nothing_happened = (
                outcome in ("failed", "stopped") and tags and details.get("tags_literal", False)
                and all(sha == "" for _t, _r, sha in found)
            )
            if nothing_happened:
                return
            self.charge("deploy-tag")
        for repo in dict.fromkeys(repo for _t, _d, _r, repo in pinned):
            self.run_credits.append(("tags", repo, frozenset(t for t, _d, _r, rp in pinned if rp == repo)))

    def released(self, classes, details: dict, output: str, outcome: str) -> None:
        """A release command that was attempted: charge what took effect (or may
        have), and credit the runs it started."""
        if outcome == "refused":
            return
        sole = bool(details.get("sole"))
        failed_alone = outcome == "failed" and sole
        at = details.get("at")
        for run_id, target_dir, slug in details.get("reruns", []):
            repo = self.repo_of(target_dir, slug)
            if failed_alone and self._not_rerun(run_id, repo, at):
                continue
            if self.rerun_credit(run_id, target_dir, slug) is not True:
                self.charge("dispatch")  # unknown is charged too: a refusal, never a release
            if repo:
                self.reruns_used.add((repo, run_id))
        if "merge" in classes:
            self._merge_effect(details, outcome)
        if "deploy-tag" in classes:
            self._tag_effect(details, outcome)
        if "push-main" in classes and not (
                failed_alone and details.get("push_single", False) and _push_refused(output)):
            pushed = _pushed_main_commit(output) if sole and outcome == "ok" else None
            dirs = details.get("push_dirs") or [""]
            remotes = details.get("push_remotes") or ["origin"]
            repo = self.repo_of(dirs[0], None, remotes[0])
            self.charge("push-main", produced=Produced(pushed, _first_dir(dirs[0]), False, repo)
                        if pushed and repo else None)
        if "dispatch" in classes and not (failed_alone and self._no_dispatch_run(details, at)):
            self.charge("dispatch")
            if sole:
                # The run's repository is the one its URL names; a bare path, the dispatch's.
                own = (details.get("dispatches") or [{}])[0]
                for m in re.finditer(r"(?:github\.com/([^/\s]+/[^/\s]+))?/actions/runs/(\d+)", output or ""):
                    repo = _repo_path(m.group(1)) if m.group(1) else self.repo_of(own.get("dir"), own.get("slug"))
                    if repo:
                        self.run_credits.append(("run", repo, m.group(2)))

    def _settled(self, at: float | None) -> bool:
        return at is not None and self.world.now() - at >= RUN_REGISTER_S

    def _not_rerun(self, run_id: str | None, repo: str | None, at: float | None) -> bool:
        """Proof that a rerun did not happen: the run's newest attempt started
        before the rerun was attempted, read after GitHub had time to register one."""
        if not run_id or not repo or not self._settled(at):
            return False
        started = self.world.run_started(run_id, repo)
        return started is not None and started < at - CLOCK_MARGIN_S

    def _no_dispatch_run(self, details: dict, at: float | None) -> bool:
        """Proof that no dispatch of the call made a run: none of its workflow
        created since the attempt (a rerun: no new attempt). Anything else, unknown."""
        targets = details.get("dispatches") or []
        if not targets or not self._settled(at):
            return False
        for t in targets:
            repo = self.repo_of(t["dir"], t["slug"])
            if repo is None:
                return False  # unpinned: no lookup proves anything
            if t.get("run"):
                if not self._not_rerun(t["run"], repo, at):
                    return False
            elif not _literal(t.get("workflow")) or self.world.workflow_runs_since(
                    t["workflow"], repo, at - CLOCK_MARGIN_S) is not False:
                return False
        return True

    def rerun_credit(self, run_id: str, target_dir: str, slug: str | None) -> bool | None:
        """True when `run_id` is a run a release of this session started and it has
        not been re-run under that credit yet; None when GitHub could not say."""
        repo = self.repo_of(target_dir, slug)
        if repo is None or (repo, run_id) in self.reruns_used:
            return False
        if ("run", repo, run_id) in self.run_credits:
            return True
        tags = set().union(*(v for k, r, v in self.run_credits if k == "tags" and r == repo))
        if not tags:
            return False
        info = self.world.run_info(run_id, repo)
        if info is None:
            return None
        return info.get("event") == "push" and info.get("head_branch") in tags

    def rerun_covered(self, run_id: str, target_dir: str, slug: str | None) -> bool:
        return self.rerun_credit(run_id, target_dir, slug) is True or self.covers("dispatch")


def build_ledger(transcript_path: str, current_tool_use_id: str | None,
                 resolver: SkillResolver, scope: RepoScope | None = None,
                 world: World | None = None) -> Ledger:
    ledger = Ledger(resolver.actions, resolver.follow_on, world)
    scope = scope or release_scope(resolver.cwd)
    pending: dict[str, tuple] = {}
    # Release commands started with `run_in_background`, waiting for their notice.
    running: dict[str, tuple[tuple, str]] = {}
    # Claude Code can write ONE typed command into the transcript twice: same
    # content, same parentUuid, a new uuid (seen 2026-10-06 — one `/approve
    # merge` counted as two approvals). Two entries at the same place in the
    # conversation with the same text are one prompt.
    seen_prompts: set[tuple] = set()

    try:
        fh = open(transcript_path, encoding="utf-8", errors="replace")
    except OSError:
        return ledger
    with fh:
        for line in fh:
            # Substring prefilter: parse only lines that can matter. Each test
            # stands alone, because a line's text may quote another line's keys.
            if not (
                "<command-name>" in line
                or ('"type":"user"' in line and '"content":"' in line)
                or '"name":"Skill"' in line
                or ('"name":"Bash"' in line and RELEASE_VERB_HINT.search(re.sub(r"[\\'\"]", "", line)))
                or (pending and '"tool_result"' in line and any(pid in line for pid in pending))
            ):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = (entry.get("message") or {}).get("content")
            if entry.get("type") == "user":
                if isinstance(content, str) and not entry.get("isSidechain"):
                    notice = parse_task_notification(content)
                    if notice and notice[0] in running:
                        effect, output = running.pop(notice[0])
                        _apply(ledger, effect, output, notice_outcome(notice[1], notice[2]))
                        continue
                if _is_human_prompt(entry) and isinstance(content, str):
                    key = (entry.get("parentUuid"), content)
                    if entry.get("parentUuid") is not None and key in seen_prompts:
                        continue
                    seen_prompts.add(key)
                    for name, args in typed_commands(content):
                        ledger.typed(name, args)
                    continue
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    effect = pending.pop(block.get("tool_use_id"), None)
                    if not effect:
                        continue
                    outcome = result_outcome(block)
                    if effect[0] == "bash" and effect[2].get("background") and outcome == "ok":
                        # "Command running in background": it has started, not ended.
                        running[block.get("tool_use_id")] = (effect, _result_text(block))
                    else:
                        _apply(ledger, effect, _result_text(block), outcome)
            elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
                for block in content or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tid = block.get("id")
                    if tid == current_tool_use_id:
                        continue
                    tool_input = block.get("input") or {}
                    if block.get("name") == "Skill":
                        name = str(tool_input.get("skill") or "")
                        if resolver.actions(name):
                            pending[tid] = ("skill", name)
                    elif block.get("name") == "Bash":
                        branch = entry.get("gitBranch")
                        details: dict = {}
                        classes = classify(
                            str(tool_input.get("command") or ""),
                            entry.get("cwd") or resolver.cwd,
                            branch_of=branch_reader(entry.get("cwd") or resolver.cwd, branch),
                            scope=scope,
                            details=details,
                            env=inherited_env(),
                        )
                        if classes:
                            details["background"] = bool(tool_input.get("run_in_background"))
                            details["at"] = _epoch(entry.get("timestamp"))
                            pending[tid] = ("bash", classes, details)
    # A tool call whose result never reached the transcript may still have run, and
    # one still running in the background may yet release: both are charged. The
    # wrong answer here is a refusal, never a release.
    for effect in pending.values():
        _apply(ledger, effect, "", "pending")
    for effect, output in running.values():
        _apply(ledger, effect, output, "pending")
    return ledger


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return ""


def _apply(ledger: Ledger, effect: tuple, output: str, outcome: str) -> None:
    if effect[0] == "skill":
        if outcome != "refused":
            ledger.skill_invoked(effect[1])
    else:
        ledger.released(effect[1], effect[2], output, outcome)


# --------------------------------------------------------------------------
# Decisions.
# --------------------------------------------------------------------------

def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _line_to_type(missing: list[str], ledger: Ledger, resolver: SkillResolver) -> str:
    """The exact `/approve ...` line that would cover `missing`: the skill this
    session is running when it declares the action, else the narrowest skill."""
    names: list[str] = []
    for action in missing:
        action = "dispatch" if action == "rerun" else action
        if ledger.active and action in resolver.actions(ledger.active):
            pick = ledger.active
        else:
            pick = next((n for n in SUGGESTION_ORDER if action in resolver.actions(n)), None)
        if pick and pick not in names:
            names.append(pick)
    return "/approve " + " ".join(names) if names else "/approve <skill>"


def _review_approves_merge(details: dict, ledger: Ledger, world: World) -> str | None:
    """None when the owner's review on GitHub approves this merge; otherwise why not,
    or "" when no review could (no App on this machine, for this repository)."""
    merges = details.get("merges") or []
    if len(merges) != 1:
        return ""
    m = merges[0]
    repo = ledger.repo_of(m["dir"], m["slug"])
    config = world.app_config
    if config is None or not config.covers(_repo_path(repo)):
        return (f"The agent App config is unusable: {world.app_config_problem}."
                if world.app_config_problem else "")
    if not _literal(m["ref"]) or not re.fullmatch(r"\d+", m["ref"] or "") or m["auto"]:
        return ("A GitHub review approves only `gh pr merge <PR> --squash --match-head-commit <SHA>` "
                "with the PR's number.")
    head = m.get("head")
    if not head or not re.fullmatch(r"[0-9a-f]{40}", head):
        return ("A GitHub review approves a merge only with `--match-head-commit <SHA>`, the full "
                "head it approved, so GitHub refuses the merge if anything was pushed since.")
    review = world.review(m["ref"], repo)
    if review is None:
        return f"The review of PR {m['ref']} could not be read from GitHub."
    if not review.approved:
        return f"GitHub review: {review.reason}."
    if review.head != head:
        return (f"GitHub review: {config.owner_login} approved {review.head[:12]}, and this merge names "
                f"{head[:12]}.")
    return None


def _review_approves_skill(name: str, payload: dict, resolver: SkillResolver, world: World) -> str | None:
    """None when the owner's review on GitHub approves the PR of this session's branch
    for a skill whose only release is that merge (ship, merge); otherwise why not."""
    if set(resolver.actions(name)) != {"merge"}:
        return ""
    config = world.app_config
    if config is None:
        return f"The agent App config is unusable: {world.app_config_problem}." if world.app_config_problem else ""
    cwd = payload.get("cwd") or os.getcwd()
    repo = world.repo_slug(cwd)
    if not config.covers(_repo_path(repo)):
        return (f"The agent App config is unusable: {world.app_config_problem}."
                if world.app_config_problem else "")
    branch = world.current_branch(cwd)
    pr = world.branch_pr(branch, repo)
    if pr is None:
        return f"GitHub review: the branch {branch or '?'} has no single open PR to approve."
    review = world.review(pr, repo)
    if review is None:
        return f"The review of PR {pr} could not be read from GitHub."
    return None if review.approved else f"GitHub review of PR {pr}: {review.reason}."


def decide_skill(payload: dict, resolver: SkillResolver, read=build_ledger, sleep=time.sleep,
                 history_scope: RepoScope | None = None, world: World | None = None) -> str | None:
    """Returns a refusal reason, or None to allow."""
    name = str((payload.get("tool_input") or {}).get("skill") or "")
    if not resolver.actions(name):
        return None
    b = bare(name)
    if payload.get("agent_id"):
        return (f"`{b}` is a release skill and runs only in the main conversation, "
                f"not inside a subagent. Return to the main conversation and invoke it there.")
    history_scope = history_scope or release_scope(payload.get("cwd") or os.getcwd())
    world = world or World()
    for attempt in range(2):
        ledger = read(payload.get("transcript_path") or "", payload.get("tool_use_id"), resolver,
                      history_scope, world)
        if ledger.skill_allowed(name):
            return None
        if attempt == 0:
            sleep(TRANSCRIPT_LAG_RETRY_S)
    review_note = _review_approves_skill(name, payload, resolver, world)
    if review_note is None:
        return None
    return (f"`{b}` is a release skill and needs the user's approval: none of `{b}` is open in this "
            f"session (an approval stays open until the release it covers has taken effect). Ask the "
            f"user to send `/approve {b}` as a message of its own (nothing else in it), or to run "
            f"`/{b}` themselves. Do not carry out its steps by hand: the release "
            f"commands themselves are gated too. Say that you were blocked."
            + (f" {review_note}" if review_note else ""))


def decide_bash(payload: dict, resolver: SkillResolver, read=build_ledger,
                sleep=time.sleep, branch_of=None,
                scope: RepoScope | None = None, history_scope: RepoScope | None = None,
                world: World | None = None) -> str | None:
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    piped: set[str] = set()
    details: dict = {}
    scope = scope or release_scope(payload.get("cwd") or os.getcwd())
    cwd = payload.get("cwd") or os.getcwd()
    classes = classify(cmd, cwd, branch_of=branch_of or branch_reader(cwd),
                       scope=scope, piped=piped, details=details, env=inherited_env())
    problem = release_shape_problem(cmd, details.get("shape_at", [])) or next(
        iter(details.get("problems", [])), None)
    if problem and not payload.get("agent_id"):
        return (f"Refused before running: write the release command as plain words, one release "
                f"per command, e.g. {RELEASE_SHAPE_EXAMPLE}, `gh pr merge <PR> --squash "
                f"--match-head-commit <SHA>` or `gh workflow run promote.yml -f sha=<SHA>`, with at "
                f"most `> /tmp/x 2>&1` after it. This one has {problem}. No quotes, variables, "
                f"`$(...)`, globs, escapes or other redirections in a release command. Nothing was used.")
    if not classes:
        return None
    what = ", ".join(sorted(classes))
    if payload.get("agent_id"):
        return (f"Blocked: this command performs a release action ({what}), and release actions "
                f"run only in the main conversation, never inside a subagent.")
    if piped:
        # A pipe reports its LAST command's exit status, so a release that failed reads as
        # one that ran. Seen 2026-10-07: a `git push origin deploy-… 2>&1 | tail -3` whose
        # push failed ("src refspec … does not match any") came back as exit 0.
        return (f"Refused before running: the output of this release action "
                f"({', '.join(sorted(piped))}) goes into a pipe. A pipe reports the exit status of "
                f"its last command, so a push or merge that fails would read as success. Run the "
                f"release command without a pipe; to keep the output short, redirect it to a file "
                f"and read that afterwards. Nothing was used.")
    if details.get("unbounded") or details.get("effects", 0) > 1:
        # One approval covers one effect of a class, and a call's effects are judged
        # together: `gh pr merge 12 && gh pr merge 13` under one approval merged two.
        bulk = details.get("unbounded_why") or []
        return (f"Refused before running: this call can perform "
                + (f"any number of release actions ({what}): it pushes with {', '.join(bulk)}. "
                   if bulk else f"{details['effects']} release actions ({what}). ")
                + f"One approval covers one release effect, so run exactly one "
                f"release command per call: one merge, one push to main, one deploy tag pushed by its "
                f"literal name (`git push origin deploy-<TS>`, never `--tags`), or one dispatch. "
                f"Nothing was used.")
    missing: list[str] = []
    review_note: str | None = None
    history_scope = history_scope or release_scope(payload.get("cwd") or os.getcwd())
    world = world or World()
    ledger: Ledger | None = None
    tag_sha: str | None = None
    tags = details.get("tags") or []
    for attempt in range(2):
        ledger = read(payload.get("transcript_path") or "", payload.get("tool_use_id"), resolver,
                      history_scope, world)
        missing = sorted(c for c in classes - {"rerun", "deploy-tag"} if not ledger.covers(c))
        if "deploy-tag" in classes and not ledger.covers("deploy-tag"):
            # Only a single deploy tag named literally can be a follow-on: its commit is
            # read from the local tag, which must exist before the push.
            # `git push origin B:refs/tags/deploy-1` pushes B, whatever the local tag says,
            # so only the bare refspec (whose source IS the local tag) qualifies.
            if details.get("tags_literal") and len(tags) == 1 and tags[0] in details.get("tag_plain", []) \
                    and ledger.armed("deploy-tag"):
                tag_sha = world.local_tag(tags[0], _first_dir(details["tag_dirs"][0]))
            tag_repo = ledger.repo_of(details["tag_dirs"][0], None, details["tag_remotes"][0]) if tags else None
            if not ledger.covers("deploy-tag", tag_sha, tag_repo):
                missing.append("deploy-tag")
        if any(not ledger.rerun_covered(*r) for r in details.get("reruns", [])):
            missing.append("rerun")
        if "merge" in missing:
            review_note = _review_approves_merge(details, ledger, world)
            if review_note is None:
                missing.remove("merge")
        if not missing:
            return None
        if attempt == 0:
            sleep(TRANSCRIPT_LAG_RETRY_S)
    assert ledger is not None
    notes = ""
    if "merge" in missing and review_note:
        notes += f" {review_note}"
    if "deploy-tag" in missing and ledger.armed("deploy-tag"):
        commits = ", ".join(sorted({p.sha[:12] for a in ledger.armed("deploy-tag") for p in a.produced}))
        notes += (f" This session's {', '.join(sorted({a.skill for a in ledger.armed('deploy-tag')}))} "
                  f"approval covers one deploy-* tag on the commit its own release produced ({commits}); "
                  f"this push is not that: "
                  + (f"its tag points at {tag_sha[:12]}." if tag_sha else
                     "create the tag on that commit first (`git tag deploy-<TS> <SHA>`), then push it "
                     "as `git push origin deploy-<TS>` (the tag's own name, no `src:dst` refspec) in a "
                     "command of its own."))
    if "rerun" in missing:
        notes += (" A `gh run rerun <id> --failed` is covered once, without a new approval, for "
                  "a run that a release approved in this session started (the deploy tag it "
                  "pushed by name, or the run URL its dispatch printed); this run is not one of "
                  "those, was already re-run once, or GitHub could not be asked.")
    return (f"Blocked: this command performs a release action ({', '.join(missing)}) that no open "
            f"approval in this session covers. An approval stays open until the release it covers "
            f"has taken effect (a merged PR, a tag on origin, a push on main, a created run), so an "
            f"earlier failed attempt did not use it; a release that took effect did. Only the user "
            f"authorises a release: ask them to send `{_line_to_type(missing, ledger, resolver)}` as a "
            f"message of its own (nothing else in it), or to run the skill itself. A request in "
            f"plain words, or an /approve line inside a longer message, is not an approval. A ref pushed through a variable "
            f"is read as the current branch, so push a tag by its literal name.{notes} Say that you "
            f"were blocked; do not route around this.")


# --------------------------------------------------------------------------
# Status (for /approve) and entry point.
# --------------------------------------------------------------------------

def status(transcript_path: str, cwd: str, scope: RepoScope | None = None,
           world: World | None = None, resolver: SkillResolver | None = None) -> str:
    resolver = resolver or SkillResolver(cwd)
    ledger = build_ledger(transcript_path, None, resolver, scope, world)
    lines = []
    if ledger.last_approve_args:
        lines.append("Last /approve named: " + ", ".join(ledger.last_approve_args))
        unknown = [n for n in ledger.last_approve_args if not resolver.actions(n)]
        if unknown:
            lines.append("Not release skills here (approve nothing): " + ", ".join(unknown))
    open_ones = [f"{a.skill} ({', '.join(sorted(k for k, v in a.left.items() if v > 0))})"
                 for a in ledger.auths if a.open()]
    lines.append("Open approvals this session (each spent only when its release takes effect): "
                 + (", ".join(open_ones) or "none"))
    for a in ledger.auths:
        for action in sorted(k for k, v in a.follow_left.items() if v > 0):
            if a.produced:
                commits = ", ".join(sorted({f"{p.sha[:12]} in {p.repo}" for p in a.produced}))
                lines.append(f"Also covered by {a.skill}: one {action} on {commits}, the commit it released.")
            elif a.open():
                lines.append(f"Also covered by {a.skill}: one {action} on the commit it will release.")
    credits = [f"run {v} in {r}" if k == "run" else "the run of " + ", ".join(sorted(v)) + f" in {r}"
               for k, r, v in ledger.run_credits]
    if credits:
        lines.append("Re-runnable once with `gh run rerun <id> --failed`: " + "; ".join(credits)
                     + (f" (already re-run: {', '.join(f'{i} in {r}' for r, i in sorted(ledger.reruns_used))})"
                        if ledger.reruns_used else ""))
    return "\n".join(lines)


def _find_transcript(session_id: str) -> str | None:
    roots = [os.environ.get("CLAUDE_CONFIG_DIR"), os.path.expanduser("~/.claude")]
    for root in filter(None, roots):
        hits = glob.glob(os.path.join(root, "projects", "*", f"{session_id}.jsonl"))
        if hits:
            return hits[0]
    return None


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--status":
        opts = dict(zip(argv[1::2], argv[2::2]))
        transcript = opts.get("--transcript") or _find_transcript(opts.get("--session", ""))
        if not transcript:
            print("No transcript found for this session.")
            return
        print(status(transcript, opts.get("--cwd") or os.getcwd()))
        return

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    tool = payload.get("tool_name")
    resolver = SkillResolver(payload.get("cwd") or os.getcwd())
    try:
        if tool == "Skill":
            reason = decide_skill(payload, resolver)
        elif tool == "Bash":
            reason = decide_bash(payload, resolver)
        else:
            return
    except Exception as exc:  # fail closed only for what could be a release
        if tool == "Bash" and not RELEASE_VERB_HINT.search(
            re.sub(r"[\\'\"]", "", str((payload.get("tool_input") or {}).get("command") or ""))
        ):
            return
        reason = f"release-approval-gate failed ({type(exc).__name__}: {exc}); refusing to be safe."
    if reason:
        deny(reason)


if __name__ == "__main__":
    main(sys.argv[1:])
