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

               merge       gh pr merge, gh api .../pulls/N/merge, and the land
                           queue: `pnpm ship:land land`, `node .../ship-land.mjs
                           land` (any subcommand but `status`/`help`), whose
                           integrator merges with a `gh pr merge` this hook
                           never sees
               push-main   git push whose destination is main/master
                           (incl. a bare push from main, --all, --mirror)
               deploy-tag  git push of a deploy-* tag, --tags, --follow-tags
               dispatch    gh workflow run, gh run rerun, gh api .../dispatches
               rerun       gh run rerun <id> --failed (exactly that form)

             Each activation of a skill (typed, or invoked after /approve)
             covers its declared actions once; a SUCCESSFUL command of that
             class uses it up, a failed one does not (so a retry still works).
             A command run in the background is judged by its completion
             notice instead of its start: `failed` did not release, anything
             else did, and one still running holds the approval. A land, in
             the foreground or the background, is released unless it exited
             1, 3 or 4, the exits after which its PR did not and will not land
             from that entry (2 and 75 promise nothing, 0 and 5 merged).
             A release that ran also earns ONE `gh run rerun <id> --failed` of
             the run it started, under the same approval: a deploy tag pushed by
             its literal name (GitHub is asked whether run <id> is that tag's
             push), or a dispatch whose own output printed the run's URL. Any
             other rerun is a dispatch and needs a dispatch approval, as before.
             Measured: 10 second approvals for a rerun since 2026-09-20, each
             followed by a 13-29 min wait for the user.
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

# The hook runs the script by path; tests load it with importlib under `python3 -I`,
# which does not put the script's directory on sys.path. Name it explicitly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_text import mask_inert, simple_commands  # noqa: E402

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
RELEASE_VERB_HINT = re.compile(r"\bgh\b|\bpush\b|\bship[-:]land\b")


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

_SEG_END = r"[^;&|\n]*"
_GIT_PUSH = re.compile(r"\bgit\b((?:[ \t]+-[Cc][ \t]+\S+|[ \t]+--?[\w-]+(?:=\S+)?)*)[ \t]+push\b(" + _SEG_END + ")")
_GH_PR_MERGE = re.compile(r"\bgh[ \t]+pr[ \t]+merge\b")
_GH_WORKFLOW_RUN = re.compile(r"\bgh[ \t]+workflow[ \t]+run\b")
_GH_RUN_RERUN = re.compile(r"\bgh[ \t]+run[ \t]+rerun\b")
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


def _asks_for_help(segment: str) -> bool:
    return any(t in ("--help", "-h") for t in _tokens(segment))


def _rerun_of_one_run(segment: str) -> tuple[str, str | None] | None:
    """(run id, -R slug) when the segment is exactly `gh run rerun <id> --failed`
    (optionally `-R owner/repo`): a re-run of the failed jobs of one named run.
    Any other form (all jobs, `--job`, `--debug`, an id computed at runtime) is
    not, and stays a plain dispatch."""
    try:
        toks = shlex.split(segment, posix=True)[3:]
    except ValueError:
        return None
    run_id, slug, failed = None, None, False
    i = 0
    while i < len(toks):
        tok = toks[i]
        if re.fullmatch(r"(?:\d*|&)>>?", tok):  # `> file`, `2>> file`: skip the target too
            i += 2
            continue
        if re.match(r"(?:\d*|&)>", tok):  # `2>&1`, `>/tmp/x`
            i += 1
            continue
        if tok == "--failed":
            failed = True
        elif tok in ("-R", "--repo") and i + 1 < len(toks):
            slug = toks[i + 1]
            i += 1
        elif tok.startswith("--repo="):
            slug = tok.split("=", 1)[1]
        elif re.fullmatch(r"\d+", tok) and run_id is None:
            run_id = tok
        else:
            return None
        i += 1
    return (run_id, slug) if run_id and failed else None


# --------------------------------------------------------------------------
# The land queue (Endoxia slice 803).
# --------------------------------------------------------------------------

# `ship:land land --pr <n>` enqueues a PR in a machine-wide queue whose integrator
# squash-merges it onto main with a `gh pr merge` run inside the script, where this
# hook never sees it. Enqueueing is therefore the merge. Its read-only subcommands
# are an allow-list; any other, and none at all, is a merge: the wrong answer is a
# refusal, never a release.
SHIP_LAND_READ_ONLY = frozenset({"status", "help"})

# The exits of `ship:land land` after which its PR did not land and will not land
# from that entry: 1 the gate on the merged union named it, 3 a textual conflict
# sent it back, 4 refused (closed, a draft, not against main, superseded by a newer
# head, being landed at its previous head, or GitHub refused the merge). Every
# other exit uses the approval: 0 and 5 merged (5: main's tree is not the one the
# queue tested); 2 (usage or environment, which can follow a `gh pr merge` whose
# answer never came) and 75 (the wait's deadline) promise nothing, since the entry
# may still be queued or mid-landing and can still merge; and so does a land that
# was killed, or whose exit cannot be read. Source: the exit contract in the header
# of Endoxia's scripts/ship-land.mjs, slice 803 D4. Re-derive when it changes.
SHIP_LAND_NOT_LANDED_EXITS = frozenset({1, 3, 4})

_SHIP_LAND_HINT = re.compile(r"\bship[-:]land\b")
_SHIP_LAND_SCRIPT = re.compile(r"ship-land\.m?js")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"env", "command", "exec", "nohup", "time", "nice", "timeout"}
_SCRIPT_RUNNERS = {"pnpm", "npm", "yarn"}
_RUNNER_DIR_FLAGS = {"-C", "--dir", "--prefix"}
# node options after which the script is not run, or that take the next token.
_NODE_NO_RUN = {"--check", "-c", "--test", "-e", "--eval", "-p", "--print"}
_NODE_VALUE_FLAGS = {"-r", "--require", "--import", "--loader", "--experimental-loader",
                     "--env-file", "--conditions", "-C", "--input-type"}


def _command_words(toks: list[str]) -> list[str]:
    """`toks` without leading assignments and wrappers (`env X=1`, `timeout 600`)."""
    i = 0
    while i < len(toks):
        if _ASSIGNMENT.match(toks[i]):
            i += 1
            continue
        wrapper = os.path.basename(toks[i])
        if wrapper not in _WRAPPERS:
            break
        i += 1
        while i < len(toks) and (toks[i].startswith("-") or _ASSIGNMENT.match(toks[i]) or (
                wrapper == "timeout" and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", toks[i]))):
            i += 1
    return toks[i:]


def _ship_land_call(toks: list[str]) -> tuple[list[str], str | None, str | None] | None:
    """(the land script's arguments, a `-C`/`--dir` directory, the script's path) when
    the simple command `toks` runs the land queue, else None."""
    toks = _command_words(toks)
    if not toks:
        return None
    program = os.path.basename(toks[0])
    if program == "ship-land" or _SHIP_LAND_SCRIPT.fullmatch(program):
        return toks[1:], None, toks[0]
    if program in _SCRIPT_RUNNERS:
        workdir = None
        for i, tok in enumerate(toks[1:], start=1):
            if tok in _RUNNER_DIR_FLAGS and i + 1 < len(toks):
                workdir = toks[i + 1]
            elif re.match(r"--(?:dir|prefix)=", tok):
                workdir = tok.split("=", 1)[1]
            elif tok == "ship:land":
                rest = toks[i + 1:]
                return (rest[1:] if rest[:1] == ["--"] else rest), workdir, None
            elif tok in ("exec", "dlx"):
                return _ship_land_call(toks[i + 1:])
        return None
    if program == "node":
        i = 1
        while i < len(toks):
            tok = toks[i]
            if tok in _NODE_NO_RUN:
                return None
            if tok in _NODE_VALUE_FLAGS:
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            if _SHIP_LAND_SCRIPT.fullmatch(os.path.basename(tok)):
                return toks[i + 1:], None, tok
            return None
    return None


def _lands(args: list[str]) -> bool:
    if any(a in ("--help", "-h") for a in args):
        return False
    subcommand = next((a for a in args if not a.startswith("-")), None)
    return subcommand not in SHIP_LAND_READ_ONLY


def _ship_lands(cmd: str, masked: str, cwd: str, scope: RepoScope) -> list[int]:
    """Start offsets of the simple commands in `cmd` that land a PR through the queue,
    in a guarded repository. Read on text whose comments and heredoc bodies are
    blank but whose quotes stay, so `node "scripts/ship-land.mjs" land` is a land and
    `echo "pnpm ship:land land"` is not."""
    if not _SHIP_LAND_HINT.search(cmd):
        return []
    quoted_kept = mask_inert(cmd, keep=lambda _c, region: region.kind in ("single", "double"))
    starts = []
    for start, end, _sep in simple_commands(masked):
        text = quoted_kept[start:end]
        if not _SHIP_LAND_HINT.search(text):
            continue
        call = _ship_land_call(_tokens(text))
        if call is None or not _lands(call[0]):
            continue
        _args, workdir, script = call
        target = _target_dir(cmd, masked, start, cwd)
        if workdir:
            target = os.path.normpath(os.path.join(target, os.path.expanduser(workdir)))
        dirs = [target]
        if script and "/" in script:  # the repository the script belongs to: <repo>/scripts/ship-land.mjs
            dirs.append(os.path.normpath(os.path.join(target, os.path.expanduser(script), os.pardir, os.pardir)))
        if any(scope.guards(d) for d in dirs):
            starts.append(start)
    return starts


def classify(cmd: str, cwd: str, branch_of=_current_branch, scope: RepoScope = GUARD_ALL,
             piped: set[str] | None = None, details: dict | None = None) -> set[str]:
    """The release action classes `cmd` performs. When `piped` is a set, the classes
    whose own command sends its output into a pipe are added to it as well. When
    `details` is a dict, it receives what a rerun credit needs: `tags` (deploy tags
    pushed by their literal name) and `reruns` ((run id, target dir, -R slug) for
    each `gh run rerun <id> --failed`), and `land` when the merge is a land."""
    classes: set[str] = set()
    if details is not None:
        details.setdefault("tags", [])
        details.setdefault("reruns", [])
    if not RELEASE_VERB_HINT.search(cmd):
        return classes
    masked = mask_inert(cmd)

    def note_pipe(m, cls: str) -> None:
        if piped is not None and _stops_at_pipe(masked, m.start()):
            piped.add(cls)

    def gh_guarded(m) -> bool:
        flag = _REPO_FLAG.search(_segment(cmd, m.start()))
        return scope.guards(_target_dir(cmd, masked, m.start(), cwd),
                            flag.group(1).strip("'\"") if flag else None)

    for m in _GH_PR_MERGE.finditer(masked):
        if gh_guarded(m) and not _asks_for_help(_segment(cmd, m.start())):
            classes.add("merge")
            note_pipe(m, "merge")
    for m in _GH_WORKFLOW_RUN.finditer(masked):
        if gh_guarded(m) and not _asks_for_help(_segment(cmd, m.start())):
            classes.add("dispatch")
            note_pipe(m, "dispatch")
    for m in _GH_RUN_RERUN.finditer(masked):
        segment = _segment(cmd, m.start())
        if not gh_guarded(m) or _asks_for_help(segment):
            continue
        one = _rerun_of_one_run(segment)
        cls = "rerun" if one else "dispatch"
        classes.add(cls)
        note_pipe(m, cls)
        if one and details is not None:
            details["reruns"].append((one[0], _target_dir(cmd, masked, m.start(), cwd), one[1]))
    if details is not None:
        # A dispatch's printed run URL becomes a rerun credit only when nothing else
        # in the call could have printed it.
        details["sole"] = not re.search(r"[;&|\n]", masked.strip())
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
            note_pipe(m, "merge")
        if "dispatches" in args:
            classes.add("dispatch")
            note_pipe(m, "dispatch")
        # The API twin of `git push origin :main`: deleting or moving the main ref.
        if re.search(r"git/refs/heads/(?:main|master)\b", args) and re.search(
                r"(?:^|\s)(?:-X|--method)[ \t=]*(?:DELETE|PATCH)\b", args, re.I):
            classes.add("push-main")
            note_pipe(m, "push-main")

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
        mine: set[str] = set()  # what THIS push does

        if flags & {"--tags", "--follow-tags"} or any(
            "deploy-" in r or "refs/tags/" in r for r in refspecs
        ):
            mine.add("deploy-tag")
            if details is not None:
                for r in refspecs:
                    name = r.lstrip("+").split(":")[-1].removeprefix("refs/tags/")
                    if name.startswith("deploy-") and "$" not in name:
                        details["tags"].append(name)
        elif any("$" in r for r in refspecs) and _DEPLOY_TAG_ASSIGN.search(cmd):
            mine.add("deploy-tag")

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
            if current_branch_needed and branch_of(repo_dir) in _MAIN_BRANCHES:
                mine.add("push-main")
        classes |= mine
        for cls in mine:
            note_pipe(m, cls)

    for start in _ship_lands(cmd, masked, cwd, scope):
        classes.add("merge")
        if details is not None:
            details["land"] = True
        if piped is not None and _stops_at_pipe(masked, start):
            piped.add("merge")
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


def background_released(effect: tuple, status: str, exit_code: int | None) -> bool:
    """Whether a release command run in the background released, by its notice. A
    `failed` one did not, the same rule as a foreground call whose result is an
    error; `completed`, `killed` and `stopped` did or may have. A land is the
    exception: a failure releases unless its exit rules the landing out."""
    if status != "failed":
        return True
    _kind, classes, details = effect
    if details.get("land"):
        return not (set(classes) == {"merge"} and exit_code in SHIP_LAND_NOT_LANDED_EXITS)
    return False


# How Claude Code records a foreground Bash call that ran and exited non-zero: an
# error result whose text starts with `Exit code <n>`, written before the command's
# own output (a timeout is `Exit code 143`). A call refused before it ran (a hook's
# deny, a permission rejection, a `<tool_use_error>`) carries no exit code.
_FOREGROUND_EXIT = re.compile(r"Exit code (\d+)\b")


def errored_released(effect: tuple, text: str) -> bool:
    """Whether a release command whose foreground result is an error released. One
    that never ran did not, and one that ran and failed did not either, except a
    land, which is judged by its exit like a background one."""
    m = _FOREGROUND_EXIT.match(text)
    return m is not None and background_released(effect, "failed", int(m.group(1)))


# One GitHub REST round trip (measured 0.53 s from this machine, 2026-10-12). The
# hook's own budget is 30 s (hooks.json) and a decision reads the transcript at
# most twice with a 1 s pause; each run id is looked up once per read, so 10 s
# per lookup keeps a slow API inside the hook budget instead of killing the hook.
GH_API_TIMEOUT_S = 10


def gh_run_info(run_id: str, target_dir: str, slug: str | None) -> dict | None:
    """The run's trigger as GitHub recorded it: {"event", "head_branch"}. For a
    tag push, `head_branch` is the tag. None when it cannot be read."""
    path = (f"repos/{slug}/actions/runs/{run_id}" if slug
            else f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}")
    cwd = target_dir if os.path.isdir(target_dir) else None
    if cwd is None and not slug:
        return None
    try:
        out = subprocess.run(["gh", "api", path, "--jq", "{event: .event, head_branch: .head_branch}"],
                             cwd=cwd, capture_output=True, text=True, timeout=GH_API_TIMEOUT_S)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


class Ledger:
    def __init__(self, actions_of=lambda _name: (), run_info=gh_run_info):
        self.approvals: Counter = Counter()  # bare skill name -> unused approvals
        self.grants: Counter = Counter()  # action class -> unused activations
        self.last_typed: str | None = None  # bare name of the last typed command
        self.last_approve_args: list[str] = []
        self._actions_of = actions_of
        # Runs a release of THIS session started, each re-runnable once with
        # `gh run rerun <id> --failed` under the approval that released it:
        # ("tags", frozenset of deploy tags pushed) or ("run", id a dispatch printed).
        self.run_credits: list[tuple[str, object]] = []
        self.reruns_used: set[str] = set()
        self._run_info = run_info
        self._info: dict[str, dict | None] = {}

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

    def rerun_credit(self, run_id: str, target_dir: str, slug: str | None) -> bool | None:
        """True when `run_id` is a run a release of this session started and it has
        not been re-run under that credit yet; None when GitHub could not say."""
        if run_id in self.reruns_used:
            return False
        if ("run", run_id) in self.run_credits:
            return True
        tags = set().union(*(v for k, v in self.run_credits if k == "tags"))
        if not tags:
            return False
        if run_id not in self._info:
            self._info[run_id] = self._run_info(run_id, target_dir, slug)
        info = self._info[run_id]
        if info is None:
            return None
        return info.get("event") == "push" and info.get("head_branch") in tags

    def rerun_covered(self, run_id: str, target_dir: str, slug: str | None) -> bool:
        return self.rerun_credit(run_id, target_dir, slug) is True or self.covers("dispatch")

    def released(self, classes, details: dict, output: str) -> None:
        """A release command that ran: use what covered it, and credit the runs it started."""
        for run_id, target_dir, slug in details.get("reruns", []):
            if self.rerun_credit(run_id, target_dir, slug) is not True:
                self.release_ran({"dispatch"})  # unknown is charged too: a refusal, never a release
            self.reruns_used.add(run_id)
        self.release_ran(set(classes) - {"rerun"})
        if "deploy-tag" in classes and details.get("tags"):
            self.run_credits.append(("tags", frozenset(details["tags"])))
        if "dispatch" in classes and details.get("sole"):
            for run_id in re.findall(r"/actions/runs/(\d+)", output or ""):
                self.run_credits.append(("run", run_id))


def build_ledger(transcript_path: str, current_tool_use_id: str | None,
                 resolver: SkillResolver, scope: RepoScope | None = None,
                 run_info=gh_run_info) -> Ledger:
    ledger = Ledger(resolver.actions, run_info)
    scope = scope or RepoScope.from_config(unknown_guarded=False)
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
                    notice = None if entry.get("isSidechain") else parse_task_notification(content)
                    if notice and notice[0] in running:
                        effect, output = running.pop(notice[0])
                        if background_released(effect, notice[1], notice[2]):
                            _apply(ledger, effect, output)
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
                    if not effect:
                        continue
                    if _is_error_result(block):
                        if effect[0] == "bash" and errored_released(effect, _result_text(block)):
                            _apply(ledger, effect, _result_text(block))
                        continue
                    if effect[0] == "bash" and effect[2].get("background"):
                        # "Command running in background": it has started, not released.
                        running[block.get("tool_use_id")] = (effect, _result_text(block))
                    else:
                        _apply(ledger, effect, _result_text(block))
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
                        details: dict = {}
                        classes = classify(
                            str(tool_input.get("command") or ""),
                            entry.get("cwd") or resolver.cwd,
                            branch_of=lambda _d, b=branch: b,
                            scope=scope,
                            details=details,
                        )
                        if classes:
                            details["background"] = bool(tool_input.get("run_in_background"))
                            pending[tid] = ("bash", classes, details)
    # A tool call whose result never reached the transcript may still have run, and
    # one still running in the background may yet release: count both as used. The
    # wrong answer here is a refusal, never a release.
    for effect in pending.values():
        _apply(ledger, effect, "")
    for effect, output in running.values():
        _apply(ledger, effect, output)
    return ledger


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return ""


def _apply(ledger: Ledger, effect: tuple, output: str) -> None:
    if effect[0] == "skill":
        ledger.skill_ran(effect[1], effect[2])
    else:
        ledger.released(effect[1], effect[2], output)


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
                scope: RepoScope | None = None, history_scope: RepoScope | None = None,
                run_info=gh_run_info) -> str | None:
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    piped: set[str] = set()
    details: dict = {}
    classes = classify(cmd, payload.get("cwd") or os.getcwd(), branch_of=branch_of,
                       scope=scope or RepoScope.from_config(unknown_guarded=True), piped=piped,
                       details=details)
    if not classes:
        return None
    what = ", ".join(sorted(classes))
    if payload.get("agent_id"):
        return (f"Blocked: this command performs a release action ({what}), and release actions "
                f"run only in the main conversation, never inside a subagent.")
    if piped:
        # A pipe reports its LAST command's exit status, so a release that failed reads as
        # one that ran, and the ledger spends the approval on it. Seen 2026-10-07: a
        # `git push origin deploy-… 2>&1 | tail -3` whose push failed ("src refspec …
        # does not match any") came back as exit 0 and used up a /deploy approval.
        return (f"Refused before running: the output of this release action "
                f"({', '.join(sorted(piped))}) goes into a pipe. A pipe reports the exit status of "
                f"its last command, so a push or merge that fails would read as success and use up "
                f"the user's approval. Run the release command without a pipe; to keep the output "
                f"short, redirect it to a file and read that afterwards. Nothing was used.")
    missing: list[str] = []
    history_scope = history_scope or RepoScope.from_config(unknown_guarded=False)
    for attempt in range(2):
        ledger = read(payload.get("transcript_path") or "", payload.get("tool_use_id"), resolver,
                      history_scope, run_info)
        missing = sorted(c for c in classes - {"rerun"} if not ledger.covers(c))
        if any(not ledger.rerun_covered(*r) for r in details.get("reruns", [])):
            missing.append("rerun")
        if not missing:
            return None
        if attempt == 0:
            sleep(TRANSCRIPT_LAG_RETRY_S)
    rerun_note = ""
    if "rerun" in missing:
        rerun_note = (" A `gh run rerun <id> --failed` is covered once, without a new approval, for "
                      "a run that a release approved in this session started (the deploy tag it "
                      "pushed by name, or the run URL its dispatch printed); this run is not one of "
                      "those, was already re-run once, or GitHub could not be asked.")
    return (f"Blocked: this command performs a release action ({', '.join(missing)}) that the "
            f"user has not approved in this session. Only the user authorises a release: they "
            f"type the skill (/ship, /quickship, /merge, /deploy, /promote, /release, /hotfix), or "
            f"approve it ahead with `/approve <skill>`, which covers that skill's release action "
            f"once (through the skill, or as this bare command). A request in plain words is not "
            f"an approval: ask the user to type it. A ref pushed through a variable is read as "
            f"the current branch, so push a tag by its literal name.{rerun_note} Say that you were "
            f"blocked; do not route around this.")


# --------------------------------------------------------------------------
# Status (for /approve) and entry point.
# --------------------------------------------------------------------------

def status(transcript_path: str, cwd: str, scope: RepoScope | None = None) -> str:
    resolver = SkillResolver(cwd)
    ledger = build_ledger(transcript_path, None, resolver, scope)
    lines = []
    if ledger.last_approve_args:
        lines.append("Last /approve named: " + ", ".join(ledger.last_approve_args))
        unknown = [n for n in ledger.last_approve_args if not resolver.actions(n)]
        if unknown:
            lines.append("Not release skills here (approve nothing): " + ", ".join(unknown))
    open_approvals = {k: v for k, v in ledger.approvals.items() if v > 0 and resolver.actions(k)}
    lines.append("Unused approvals this session: " + (
        ", ".join(f"{k} x{v}" for k, v in sorted(open_approvals.items())) or "none"))
    credits = [f"run {v}" if k == "run" else "the run of " + ", ".join(sorted(v))
               for k, v in ledger.run_credits]
    if credits:
        lines.append("Re-runnable once with `gh run rerun <id> --failed`: " + "; ".join(credits)
                     + (f" (already re-run: {', '.join(sorted(ledger.reruns_used))})"
                        if ledger.reruns_used else ""))
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
