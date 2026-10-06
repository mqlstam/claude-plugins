#!/usr/bin/env python3
"""PreToolUse(Skill|Bash) gate: a release happens only when the USER approved it.

Two ways for the user to approve, both a line the user typed:

  * the old way: type the release skill itself (/ship, /deploy, ...);
  * ahead of time: type `/approve <skill> [<skill> ...]`, after which the model
    may invoke each named skill ONCE in this session (e.g. inside a /goal run).

Two layers, one script:

  Skill arm  A skill whose SKILL.md declares `release-actions:` is a release
             skill. The model may invoke it only while an unused /approve names
             it. Nothing else changes: a skill without the key is untouched.

  Bash arm   The floor. A command that performs a release action is refused
             unless a release skill that covers that action is active:

               merge       gh pr merge, gh api .../pulls/N/merge
               push-main   git push whose destination is main/master
                           (incl. a bare push from main, --all, --mirror)
               deploy-tag  git push of a deploy-* tag, --tags, --follow-tags
               dispatch    gh workflow run, gh run rerun, gh api .../dispatches

             Each activation of a skill (typed, or invoked after /approve)
             covers its declared actions once; a SUCCESSFUL command of that
             class uses it up, a failed one does not (so a retry still works).
             An unused `/approve <skill>` also covers one bare command of that
             skill's actions. The arm guards only the repositories named in
             release-approval-gate.json (the command's target: a preceding
             `cd`, `git -C`, `gh -R`, else the session cwd); other repos keep
             merging freely. The Skill arm is not scoped.
             Without this arm the Skill arm is a signpost in front of an open
             door: the model can type the same `gh pr merge` itself, and in a
             /goal run on 2026-10-05 it did exactly that.

Why the approval is read from the TRANSCRIPT and not from a file: a file the
gate trusts is a file the model can write. A typed slash command is recorded as
a `user` entry whose whole content is the `<command-name>` envelope; the model's
own words land in `assistant` entries, tool output in `tool_result` blocks,
skill bodies in `isMeta` entries, compaction summaries in `isCompactSummary`
entries and background-task notices start with `<task-notification>`. None of
those can satisfy the anchored envelope match below.

What it does NOT buy, stated so nobody over-trusts it:
  * A determined model can hide a release command (a script file, `sh -c` with
    a built string, a variable holding `main`, curl to the GitHub API). This
    turns SILENT into MUST-ACTIVELY-EVADE, which is what a guardrail against a
    cooperative agent needs to do.
  * Hooks are snapshotted at session start: an edit to this file takes effect
    in the next session (or after /reload-plugins).
  * An approval typed while Claude is busy is queued as a prompt, not run as a
    command, so it does not count. Type /approve while Claude is idle.
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


def _git_toplevel(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _current_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def read_release_actions(skill_md: str) -> tuple[str, ...]:
    try:
        with open(skill_md) as fh:
            text = fh.read()
    except OSError:
        return ()
    if not text.startswith("---"):
        return ()
    end = text.find("\n---", 3)
    front = text[3:end if end != -1 else len(text)]
    m = re.search(r"^release-actions:[ \t]*(.+)$", front, re.M)
    if not m:
        return ()
    raw = m.group(1).strip().strip("[]")
    return tuple(sorted({
        v.strip().strip("'\"") for v in re.split(r"[,\s]+", raw) if v.strip().strip("'\"")
    }))


class SkillResolver:
    """Maps a skill name (as typed, or as the Skill tool receives it) to the
    release actions its SKILL.md declares. Empty = not a release skill."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self._cache: dict[str, tuple[str, ...]] = {}
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

    def actions(self, name: str) -> tuple[str, ...]:
        key = name.strip().lstrip("/")
        if key not in self._cache:
            found: tuple[str, ...] = ()
            for path in self.candidates(key):
                if os.path.isfile(path):
                    found = read_release_actions(path)
                    break
            self._cache[key] = found
        return self._cache[key]


# --------------------------------------------------------------------------
# Bash classification.
# --------------------------------------------------------------------------

def mask_inert(cmd: str) -> str:
    """Same-length copy of `cmd` with quoted strings and heredoc bodies blanked,
    so `echo "... gh pr merge ..."` or a PR body is not read as a command, while
    match offsets still index the raw string."""
    out = list(cmd)
    i, n = 0, len(cmd)
    pending_heredocs: list[tuple[str, bool]] = []
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            j = cmd.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "'":
            j = cmd.find("'", i + 1)
            j = n - 1 if j == -1 else j
            for k in range(i + 1, j):
                out[k] = " "
            i = j + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and cmd[j] != '"':
                j += 2 if cmd[j] == "\\" else 1
            j = min(j, n - 1)
            for k in range(i + 1, j):
                out[k] = " "
            i = j + 1
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = re.match(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", cmd[i:])
            if m:
                pending_heredocs.append((m.group(3), m.group(1) == "-"))
                i += m.end()
                continue
        if ch == "\n" and pending_heredocs:
            j = i + 1
            while pending_heredocs and j <= n:
                word, dash = pending_heredocs.pop(0)
                while j < n:
                    line_end = cmd.find("\n", j)
                    line_end = n if line_end == -1 else line_end
                    line = cmd[j:line_end]
                    if (line.strip() if dash else line) == word:
                        j = line_end + 1
                        break
                    for k in range(j, line_end):
                        out[k] = " "
                    j = line_end + 1
            i = j
            continue
        i += 1
    return "".join(out)


_SEG_END = r"[^;&|\n]*"
_GIT_PUSH = re.compile(r"\bgit\b((?:[ \t]+-[Cc][ \t]+\S+|[ \t]+--?[\w-]+(?:=\S+)?)*)[ \t]+push\b(" + _SEG_END + ")")
_GH_PR_MERGE = re.compile(r"\bgh[ \t]+pr[ \t]+merge\b")
_GH_WORKFLOW_RUN = re.compile(r"\bgh[ \t]+(?:workflow[ \t]+run|run[ \t]+rerun)\b")
_GH_API = re.compile(r"\bgh[ \t]+api\b(" + _SEG_END + ")")
_DEPLOY_TAG_ASSIGN = re.compile(r"\b\w+=[\"']?deploy-")
_PUSH_FLAGS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
_MAIN_BRANCHES = {"main", "master"}


def _tokens(raw: str) -> list[str]:
    try:
        toks = shlex.split(raw, posix=True)
    except ValueError:
        toks = raw.split()
    return [t for t in toks if not re.match(r"^\d*>", t)]


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

_CD = re.compile(r"(?:^|[;&|(\n])[ \t]*cd[ \t]+(\S+)")
_REPO_FLAG = re.compile(r"(?:^|\s)(?:-R|--repo)(?:[ \t]+|=)(\S+)")


def slug_of_url(url: str) -> str | None:
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def _origin_slug(path: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return slug_of_url(out.stdout) if out.returncode == 0 else None


class RepoScope:
    """Decides whether a release command's TARGET repository is guarded.
    `unknown_guarded`: what to answer when the target cannot be named — True
    for the command being decided (refuse), False when replaying history (a
    deleted /tmp clone must not use up a guarded repo's approval)."""

    def __init__(self, guarded: set[str] | None, unknown_guarded: bool, slug_of_dir=_origin_slug):
        self.guarded = guarded  # None = every repo
        self.unknown_guarded = unknown_guarded
        self._slug_of_dir = slug_of_dir
        self._cache: dict[str, str | None] = {}

    @classmethod
    def from_config(cls, unknown_guarded: bool) -> "RepoScope":
        try:
            with open(SCOPE_CONFIG) as fh:
                repos = json.load(fh).get("guardedRepos")
            guarded = {str(r).lower() for r in repos} if isinstance(repos, list) else None
        except (OSError, ValueError, AttributeError):
            guarded = None
        return cls(guarded, unknown_guarded)

    def dir_slug(self, path: str) -> str | None:
        if path not in self._cache:
            self._cache[path] = self._slug_of_dir(path) if os.path.isdir(path) else None
        return self._cache[path]

    def guards(self, target_dir: str, explicit_slug: str | None = None) -> bool:
        if self.guarded is None:
            return True
        slug = explicit_slug.lower().removesuffix(".git") if explicit_slug else self.dir_slug(target_dir)
        if slug is None:
            return self.unknown_guarded
        return slug in self.guarded


GUARD_ALL = RepoScope(None, True)


def _target_dir(raw: str, masked: str, pos: int, cwd: str) -> str:
    """The directory a command at `pos` runs in: the last `cd` before it."""
    target = cwd
    for m in _CD.finditer(masked[:pos]):
        arg = raw[m.start(1):m.end(1)].strip("'\"")
        if arg in ("-",) or "$" in arg:
            continue
        target = os.path.normpath(os.path.join(target, os.path.expanduser(arg)))
    return target


def _segment(raw: str, start: int) -> str:
    m = re.match(_SEG_END, raw[start:])
    return raw[start:start + (m.end() if m else 0)]


def classify(cmd: str, cwd: str, branch_of=_current_branch, scope: RepoScope = GUARD_ALL) -> set[str]:
    classes: set[str] = set()
    if not RELEASE_VERB_HINT.search(cmd):
        return classes
    masked = mask_inert(cmd)

    def gh_guarded(m) -> bool:
        flag = _REPO_FLAG.search(_segment(cmd, m.start()))
        return scope.guards(_target_dir(cmd, masked, m.start(), cwd),
                            flag.group(1).strip("'\"") if flag else None)

    for m in _GH_PR_MERGE.finditer(masked):
        if gh_guarded(m):
            classes.add("merge")
    for m in _GH_WORKFLOW_RUN.finditer(masked):
        if gh_guarded(m):
            classes.add("dispatch")
    for m in _GH_API.finditer(masked):
        args = cmd[m.start(1):m.end(1)]
        api_slug = re.search(r"repos/([^/\s]+/[^/\s]+)/", args)
        if api_slug and ":owner" not in api_slug.group(1):
            guarded = scope.guards(cwd, api_slug.group(1))
        else:
            guarded = gh_guarded(m)
        if not guarded:
            continue
        if re.search(r"pulls/[^/\s]+/merge\b|/merges\b", args):
            classes.add("merge")
        if "dispatches" in args:
            classes.add("dispatch")

    for m in _GIT_PUSH.finditer(masked):
        git_opts = _tokens(cmd[m.start(1):m.end(1)])
        repo_dir = _target_dir(cmd, masked, m.start(), cwd)
        for k, tok in enumerate(git_opts[:-1]):
            if tok == "-C":
                repo_dir = os.path.normpath(os.path.join(repo_dir, os.path.expanduser(git_opts[k + 1])))
        if not scope.guards(repo_dir):
            continue
        toks = _tokens(cmd[m.start(2):m.end(2)])
        positional: list[str] = []
        skip = False
        flags: set[str] = set()
        for tok in toks:
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

        if flags & {"--tags", "--follow-tags"} or any(
            "deploy-" in r or "refs/tags/" in r for r in refspecs
        ):
            classes.add("deploy-tag")
        elif any("$" in r for r in refspecs) and _DEPLOY_TAG_ASSIGN.search(cmd):
            classes.add("deploy-tag")

        if flags & {"--all", "--mirror"}:
            classes.add("push-main")
            continue
        current_branch_needed = not refspecs and not (flags & {"--tags", "--follow-tags"})
        for ref in refspecs:
            dst = ref.lstrip("+").split(":")[-1]
            dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
            if dst in _MAIN_BRANCHES:
                classes.add("push-main")
            elif dst == "HEAD" or ("$" in dst and "deploy-tag" not in classes):
                current_branch_needed = True
        if current_branch_needed and branch_of(repo_dir) in _MAIN_BRANCHES:
            classes.add("push-main")
    return classes


# --------------------------------------------------------------------------
# The ledger: what the user approved, what has been used.
# --------------------------------------------------------------------------

_ENVELOPE_TAG = re.compile(r"<(command-name|command-message|command-args)>(.*?)</\1>", re.S)


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


def _is_human_prompt(entry: dict) -> bool:
    return (
        entry.get("type") == "user"
        and not entry.get("isMeta")
        and not entry.get("isSidechain")
        and not entry.get("isCompactSummary")
    )


def _is_error_result(block: dict) -> bool:
    if block.get("is_error") is True:
        return True
    content = block.get("content")
    return isinstance(content, str) and content.lstrip().startswith("<tool_use_error>")


def approval_names(args: str) -> list[str]:
    return [bare(t) for t in re.split(r"[,\s]+", args) if bare(t)]


class Ledger:
    def __init__(self, actions_of=lambda _name: ()):
        self.approvals: Counter = Counter()  # bare skill name -> unused approvals
        self.grants: Counter = Counter()  # action class -> unused activations
        self.last_typed: str | None = None  # bare name of the last typed command
        self.last_approve_args: list[str] = []
        self._actions_of = actions_of

    def approval_covering(self, action: str) -> str | None:
        """An unused approval of a skill whose declared actions include
        `action`. `/approve merge` thereby also covers one bare `gh pr merge`
        in a repo where the skill itself does not fit (a website repo, say)."""
        for name in sorted(n for n, k in self.approvals.items() if k > 0):
            if action in self._actions_of(name):
                return name
        return None

    def covers(self, action: str) -> bool:
        return self.grants[action] > 0 or self.approval_covering(action) is not None

    def typed(self, name: str, args: str, actions: tuple[str, ...]) -> None:
        b = bare(name)
        self.last_typed = b
        if b == "approve":
            names = approval_names(args)
            self.last_approve_args = names
            for n in names:
                self.approvals[n] += 1
            return
        if actions:
            self.grants.update(actions)
            if self.approvals[b] > 0:  # the user ran it themselves: approval used
                self.approvals[b] -= 1

    def skill_ran(self, name: str, actions: tuple[str, ...]) -> None:
        b = bare(name)
        # Only an approved run activates the skill's actions. A run let through
        # because the user had just typed the same skill is that typed run,
        # which already granted them once.
        if self.approvals[b] > 0:
            self.approvals[b] -= 1
            self.grants.update(actions)

    def release_ran(self, classes) -> None:
        for c in sorted(classes):
            if self.grants[c] > 0:
                self.grants[c] -= 1
                continue
            name = self.approval_covering(c)
            if name:
                self.approvals[name] -= 1


def build_ledger(transcript_path: str, current_tool_use_id: str | None,
                 resolver: SkillResolver, scope: RepoScope | None = None) -> Ledger:
    ledger = Ledger(resolver.actions)
    scope = scope or RepoScope.from_config(unknown_guarded=False)
    pending: dict[str, tuple] = {}
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
                # every plain-text user prompt: it ends the "just typed" state
                or ('"type":"user"' in line and '"content":"' in line)
                or '"name":"Skill"' in line
                or ('"name":"Bash"' in line and RELEASE_VERB_HINT.search(line))
                or (pending and '"tool_result"' in line and any(pid in line for pid in pending))
            ):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = (entry.get("message") or {}).get("content")
            if entry.get("type") == "user":
                if isinstance(content, str):
                    if _is_human_prompt(entry):
                        key = (entry.get("parentUuid"), content)
                        if entry.get("parentUuid") is not None and key in seen_prompts:
                            continue
                        seen_prompts.add(key)
                        cmd = parse_typed_command(content)
                        if cmd:
                            ledger.typed(cmd[0], cmd[1], resolver.actions(cmd[0]))
                        else:
                            ledger.last_typed = None
                    continue
                for block in content or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    effect = pending.pop(block.get("tool_use_id"), None)
                    if effect and not _is_error_result(block):
                        _apply(ledger, effect)
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
                        actions = resolver.actions(name)
                        if actions:
                            pending[tid] = ("skill", name, actions)
                    elif block.get("name") == "Bash":
                        branch = entry.get("gitBranch")
                        classes = classify(
                            str(tool_input.get("command") or ""),
                            entry.get("cwd") or resolver.cwd,
                            branch_of=lambda _d, b=branch: b,
                            scope=scope,
                        )
                        if classes:
                            pending[tid] = ("bash", classes)
    # A tool call whose result never reached the transcript may still have run:
    # count it as used. The wrong answer here is a refusal, never a release.
    for effect in pending.values():
        _apply(ledger, effect)
    return ledger


def _apply(ledger: Ledger, effect: tuple) -> None:
    if effect[0] == "skill":
        ledger.skill_ran(effect[1], effect[2])
    else:
        ledger.release_ran(effect[1])


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


def decide_skill(payload: dict, resolver: SkillResolver, read=build_ledger, sleep=time.sleep,
                 history_scope: RepoScope | None = None) -> str | None:
    """Returns a refusal reason, or None to allow."""
    name = str((payload.get("tool_input") or {}).get("skill") or "")
    if not resolver.actions(name):
        return None
    b = bare(name)
    if payload.get("agent_id"):
        return (f"`{b}` is a release skill and runs only in the main conversation, "
                f"not inside a subagent. Return to the main conversation and invoke it there.")
    history_scope = history_scope or RepoScope.from_config(unknown_guarded=False)
    for attempt in range(2):
        ledger = read(payload.get("transcript_path") or "", payload.get("tool_use_id"), resolver,
                      history_scope)
        if ledger.approvals[b] > 0 or ledger.last_typed == b:
            return None
        if attempt == 0:
            sleep(TRANSCRIPT_LAG_RETRY_S)
    return (f"`{b}` is a release skill and needs the user's approval. Ask the user to type "
            f"`/approve {b}` (approves one run of it in this session), or to type `/{b}` "
            f"themselves. Do not carry out its steps by hand: the release commands themselves "
            f"are gated too. Say that you were blocked.")


def decide_bash(payload: dict, resolver: SkillResolver, read=build_ledger,
                sleep=time.sleep, branch_of=_current_branch,
                scope: RepoScope | None = None, history_scope: RepoScope | None = None) -> str | None:
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    classes = classify(cmd, payload.get("cwd") or os.getcwd(), branch_of=branch_of,
                       scope=scope or RepoScope.from_config(unknown_guarded=True))
    if not classes:
        return None
    what = ", ".join(sorted(classes))
    if payload.get("agent_id"):
        return (f"Blocked: this command performs a release action ({what}), and release actions "
                f"run only in the main conversation, never inside a subagent.")
    missing: list[str] = []
    history_scope = history_scope or RepoScope.from_config(unknown_guarded=False)
    for attempt in range(2):
        ledger = read(payload.get("transcript_path") or "", payload.get("tool_use_id"), resolver,
                      history_scope)
        missing = sorted(c for c in classes if not ledger.covers(c))
        if not missing:
            return None
        if attempt == 0:
            sleep(TRANSCRIPT_LAG_RETRY_S)
    return (f"Blocked: this command performs a release action ({', '.join(missing)}) that the "
            f"user has not approved in this session. Only the user authorises a release: they "
            f"type the skill (/ship, /quickship, /merge, /deploy, /promote, /release, /hotfix), or "
            f"approve it ahead with `/approve <skill>`, which covers that skill's release action "
            f"once (through the skill, or as this bare command). A request in plain words is not "
            f"an approval: ask the user to type it. A ref pushed through a variable is read as "
            f"the current branch, so push a tag by its literal name. Say that you were blocked; "
            f"do not route around this.")


# --------------------------------------------------------------------------
# Status (for /approve) and entry point.
# --------------------------------------------------------------------------

def status(transcript_path: str, cwd: str) -> str:
    resolver = SkillResolver(cwd)
    ledger = build_ledger(transcript_path, None, resolver)
    lines = []
    if ledger.last_approve_args:
        lines.append("Last /approve named: " + ", ".join(ledger.last_approve_args))
        unknown = [n for n in ledger.last_approve_args if not resolver.actions(n)]
        if unknown:
            lines.append("Not release skills here (approve nothing): " + ", ".join(unknown))
    open_approvals = {k: v for k, v in ledger.approvals.items() if v > 0 and resolver.actions(k)}
    lines.append("Unused approvals this session: " + (
        ", ".join(f"{k} x{v}" for k, v in sorted(open_approvals.items())) or "none"))
    return "\n".join(lines)


def _find_transcript(session_id: str) -> str | None:
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl"))
    return hits[0] if hits else None


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
            str((payload.get("tool_input") or {}).get("command") or "")
        ):
            return
        reason = f"release-approval-gate failed ({type(exc).__name__}: {exc}); refusing to be safe."
    if reason:
        deny(reason)


if __name__ == "__main__":
    main(sys.argv[1:])
