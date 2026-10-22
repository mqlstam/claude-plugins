#!/usr/bin/env python3
"""PreToolUse(Bash) guard: three command shapes that cost hours and read as success.

ADVISORY, NOT A BOUNDARY. This hook reads command TEXT, and shell is Turing-complete:
no reading of text can be complete, so judge it as a habit guard that catches the
shapes agents actually write, never as the thing that holds a contract. The hard
contract of a release verdict (rule 3) lives in release-run.py, in code that runs
however the command was spelled or started: an `await` without `--slice-min`, or
with one above `FOREGROUND_SLICE_MIN`, exits 2 before it waits; and an `await`
returns within its slice plus one `gh` call even when GitHub hangs (every call's
timeout is what is left of the stop). So an await that slips past this hook into
the background or a detached job loses at most one slice of verdict, and the
skill's next foreground slice reads it (scripts/tests/test_release_waits.py:
`test_await_requires_a_foreground_slice`, `test_a_slice_is_at_most_one_foreground_call`,
`test_a_hung_api_keeps_the_slice_and_the_deadline`, `test_the_reason_reads_share_the_slice`,
`test_a_hung_gh_is_killed_at_its_timeout`). The security boundary of releases is
the release-approval gate.

It runs in every session and every subagent, because these shapes kept coming
back after a skill or memory note told the model not to write them: a rule in
text reaches the main session at best, and 70% of the measured cases came from
subagents and workflow agents, which start fresh.

Rule 1, foreground waiting. Refused when the call is NOT `run_in_background`:

  * the command sleeps more than SETTLE_BOUND_S seconds in total
    (`sleep 190; cat <task output>`);
  * a `sleep` inside a loop body (`until grep -q exit log; do sleep 15; done`),
    or `watch`: a loop's total wait cannot be read from its text;
  * `gh run watch` / `gh pr checks --watch`: a foreground watch of an event
    that ends with a notification when it runs in the background.

  The fix is always the same: start the long work (or the wait itself) with
  `run_in_background: true` and act on the completion notification. A short
  settle pause after a signal you just sent (kill, cancel, restart) stays
  allowed in the foreground. A release run's verdict is the exception (rule 3).

Rule 2, a status that is not the command's. Refused in either mode, unless the
command sets `pipefail`:

  * a pipeline ending in a filter (`tail`, `head`, `sed`, ...) whose status is
    then used: `build | tail -4 && up -d`, `test | tail; echo exit=$?`,
    `if gate | tail; then`. A pipeline reports its LAST command's status, so
    the build's failure becomes the filter's 0. Reading output through a pipe
    is fine; deciding on the pipe's status is what is refused.

Rule 3, a release verdict read where it can be lost. Every rule first removes
line continuations as Bash does (a backslash-newline outside single quotes and
comments). Then, for a command whose de-quoted text names release-run and `await`
anywhere (inside `$(…)`, backticks, `-c` strings, heredocs and control-flow bodies
alike):

  * with `run_in_background` it is refused, failing closed: a mere mention too;
  * in the foreground it is refused when a text it executes (the command, each
    substitution's body, `eval`'s words, a shell's `-c` string or heredoc) holds a
    lone `&` operator on its shell token stream (a quoted `&` in a file name is a
    word, not an operator) or runs a detacher in command position, matched by
    basename (`setsid`, `/usr/bin/setsid`, `env X=1 setsid`; `disown`, `coproc`,
    `screen`, `tmux`, `daemonize`): both return before the verdict;
  * an await there needs `--slice-min`.

  A background shell dies with its Claude Code session, and its result with it.
  On 2026-10-20 the await of release run 39507708450 started in the background at
  22:23:11Z; the T3 thread settled on its PR's merge when the turn ended, Claude
  Code killed the wait at 22:23:18Z, and the run's failure at 22:45Z went unseen
  until 08:12Z. The await reads the verdict in the foreground, in slices
  (`--slice-min 9`, the Bash tool's `timeout` at 600000), and is run again on
  exit 6. The release skills say so; this rule holds it for every session.

  The foreground checks read each simple command with the release gate's own
  tokenizer (quotes and escapes resolved: `release-"run".py`), and only command
  position counts, found behind assignments, control-flow words, env/command/
  builtin/exec/nohup/timeout/sudo, xargs and a wrapper script (`bash scripts/wt.sh`):
  an await is release-run.py run there, or the script file a Python interpreter
  there runs. A foreground mention among another program's arguments (`grep -e
  release-run.py await`, `echo setsid`), a commit message or a heredoc written to a
  file runs nothing and passes. What this reading cannot see (a path held in a
  variable, `python -m`, a name assembled when the command runs) is the hard
  contract's to hold, above.

A crash in this script allows the command: it guards habits, not security, and
must never be the reason work stops. The release gate is the security guard.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import math
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_text import Region, inert_regions, mask_inert, simple_commands  # noqa: E402

# The longest foreground sleep a command may hold, summed over its `sleep`s.
#
# Derived from every foreground Bash call with a `sleep` in this machine's Claude
# Code transcripts, 2026-09-01 to 2026-10-12 (3,589 calls, 105.8 h of wall clock;
# 1,797 of them in subagents). Provenance: the owner's own development sessions,
# which is exactly the population this hook governs.
#   * Calls whose longest sleep is at most 10 s, outside a loop: 855 calls, 3.3 h.
#     They follow a signal the agent just sent and that has no completion event:
#     `kill …; sleep 1`,
#     `gh run cancel …; sleep 5`, a Docker relaunch, `sleep 8` before re-reading
#     a status GitHub recomputes asynchronously.
#   * Single sleeps above 10 s: 2,277 calls, 93.8 h, 89% of all sleep time. The
#     dominant shape is `sleep N; <read a background task's output or a run's
#     status>`, i.e. polling an event that already ends with a notification.
#   * The Bash tool's own default timeout is 120 s; 77 sleeps died on a timeout.
# Re-derive when a refusal is reported on a legitimate settle pause, or when the
# Bash tool gains a native wait.
SETTLE_BOUND_S = 10.0

_SHELL_RUNNER = re.compile(r"(?:^|[\s/])(ssh|bash|sh|zsh|dash|ksh|eval|watch)(?=\s|$)")
_SEPARATORS = ";&|(\n"


def _segment_start(text: str, pos: int) -> int:
    i = pos - 1
    while i >= 0 and text[i] not in _SEPARATORS:
        i -= 1
    return i + 1


def _runs_shell_text(cmd: str, masked_all: str) -> "callable":
    """A quoted string or heredoc body is a COMMAND when the simple command it
    belongs to hands it to a shell: `bash -c '...'`, `ssh host '...'`,
    `ssh host 'bash -s' <<EOF`, `docker exec c sh -c '...'`, `timeout 600 bash -c`."""

    def keep(_cmd: str, region: Region) -> bool:
        if region.kind == "comment":
            return False
        start = _segment_start(masked_all, region.anchor)
        return bool(_SHELL_RUNNER.search(masked_all[start:region.anchor]))

    return keep


def visible_text(cmd: str) -> str:
    masked_all = mask_inert(cmd)
    return mask_inert(cmd, keep=_runs_shell_text(cmd, masked_all))


_CMD_WORD_BEFORE = {"do", "then", "else", "nohup", "exec", "time", "command"}


def _in_command_position(text: str, pos: int) -> bool:
    i = pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i < 0:
        return True
    if text[i] in ";&|({\n'\"`":
        return True
    j = i
    while j >= 0 and (text[j].isalnum() or text[j] == "_"):
        j -= 1
    return text[j + 1:i + 1] in _CMD_WORD_BEFORE


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_UNIT_S = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _sleep_seconds(args: str) -> float | None:
    """Total seconds of `sleep <args>`, or None when an argument is computed at
    runtime (`sleep "$DELAY"`): unknown is not refused, it cannot be derived."""
    total = 0.0
    for tok in args.split():
        if tok.lower() in ("inf", "infinity"):
            return math.inf
        m = _DURATION.match(tok)
        if not m:
            return None
        total += float(m.group(1)) * _UNIT_S[m.group(2)]
    return total


_LOOP_KEYWORD = re.compile(r"(?:^|[;\n&|(])[ \t]*(do|done)(?=[\s;)]|$)")


def _loop_depth_at(text: str, pos: int) -> int:
    depth = 0
    for m in _LOOP_KEYWORD.finditer(text[:pos]):
        depth += 1 if m.group(1) == "do" else -1
    return max(depth, 0)


def foreground_wait(cmd: str) -> str | None:
    """Rule 1. The reason a foreground command waits too long, or None."""
    text = visible_text(cmd)
    total = 0.0
    shown: list[str] = []
    for m in re.finditer(r"\bsleep\b", text):
        if not _in_command_position(text, m.start()):
            continue
        rest = re.match(r"[^;&|)\n'\"`]*", text[m.end():]).group(0)
        if _loop_depth_at(text, m.start()) > 0:
            return f"it sleeps inside a loop (`sleep{rest.rstrip()}` between `do` and `done`)"
        seconds = _sleep_seconds(rest)
        if seconds is not None:
            total += seconds
            shown.append(f"sleep{rest.rstrip()}")
    if total > SETTLE_BOUND_S:
        return (f"it sleeps {('for ever' if math.isinf(total) else f'{total:g} s')} in the "
                f"foreground ({'; '.join(shown)}), above the {SETTLE_BOUND_S:g} s settle pause")
    for m in re.finditer(r"\bwatch\b", text):
        if _in_command_position(text, m.start()):
            return "it runs `watch`, a loop that never ends on its own"
    if re.search(r"\bgh[ \t]+run[ \t]+watch\b", text):
        return "it runs `gh run watch` in the foreground"
    for m in re.finditer(r"\bgh[ \t]+pr[ \t]+checks\b[^;&|\n]*", text):
        if re.search(r"(?:^|\s)--watch\b", m.group(0)):
            return "it runs `gh pr checks --watch` in the foreground"
    return None


# Commands whose exit status says nothing about their input.
_FILTERS = {"tail", "head", "cut", "sed", "awk", "tee", "sort", "uniq", "wc", "tr",
            "column", "cat", "nl", "fold"}
_PIPEFAIL = re.compile(r"\bset\s+-\w*o\s+pipefail\b|\bset\s+-o\s+pipefail\b|\bsetopt\s+pipe_?fail\b", re.I)
_STATUS_IGNORED = re.compile(r"^\s*(true|:|exit\s+0)\s*$")


def _first_word(text: str) -> str:
    for tok in text.split():
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok) or tok in ("command", "\\", "!"):
            continue
        return tok.rsplit("/", 1)[-1]
    return ""


def masked_status(cmd: str) -> str | None:
    """Rule 2. The reason a pipeline's status is read as its producer's, or None."""
    if "|" not in cmd:
        return None
    masked = mask_inert(cmd)
    if _PIPEFAIL.search(masked):
        return None
    cmds = simple_commands(masked)
    k = 0
    while k < len(cmds):
        j = k
        while cmds[j][2] in ("|", "|&") and j + 1 < len(cmds):
            j += 1
        if j > k:  # cmds[k..j] is a pipeline
            producer = masked[cmds[k][0]:cmds[k][1]].strip()
            last = _first_word(masked[cmds[j][0]:cmds[j][1]])
            sep = cmds[j][2]
            words_before = producer.split()[:1]
            consumed = None
            # A producer run as `cmd 2>&1 | filter` is a process whose whole output is
            # being summarised, i.e. one whose outcome is the point; a reader such as
            # `grep … | head && sed …` chains reads whose status nobody depends on.
            captured = cmds[k][2] == "|&" or re.search(r"2>&1\s*$", masked[cmds[k][0]:cmds[k][1]])
            if last in _FILTERS:
                if words_before and words_before[0] in ("if", "elif", "while", "until", "!"):
                    consumed = f"`{words_before[0]}`"
                elif sep == "&&" and captured:
                    consumed = "`&&`"
                elif sep == "||" and captured and j + 1 < len(cmds) and not _STATUS_IGNORED.match(
                        masked[cmds[j + 1][0]:cmds[j + 1][1]]):
                    consumed = "`||`"
                elif sep in (";", "\n") and j + 1 < len(cmds) and "$?" in cmd[cmds[j + 1][0]:cmds[j + 1][1]]:
                    consumed = "`$?`"
            if consumed:
                shown = re.sub(r"\s+", " ", cmd[cmds[k][0]:cmds[j][1]].strip())[:80]
                return (f"the status of `{shown}` is used by {consumed}, but a pipeline's status is "
                        f"its last command's: `{last}` exits 0 when the command before it failed")
        k = j + 1
    return None


def _plain(text: str) -> str:
    """The text with quotes and backslashes removed: `release-"run".py` and
    `release\\-run.py` both read `release-run.py`."""
    return re.sub(r"[\\'\"]", "", text)


@functools.lru_cache(maxsize=1)
def _gate():
    """The release gate's own reading of a command: its tokenizer (redirections removed,
    quotes and escapes resolved), its wrapper and assignment tables, and its reading of
    a shell's `-c` string or standard input. Rule 3 reads a call as the gate reads a
    release, so one parser decides both. Loaded only for a command that names
    release-run."""
    spec = importlib.util.spec_from_file_location(
        "release_approval_gate", os.path.join(os.path.dirname(os.path.abspath(__file__)), "release-approval-gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# xargs's options that take their value as the next word (GNU and BSD xargs(1)).
_XARGS_WITH_VALUE = {"-a", "-d", "-E", "-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s"}
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?$")
# python's options that take their value as the next word (python(1)). `-c` and `-m`
# run code or a module, not a script file: out of this guard's reach.
_PYTHON_WITH_VALUE = {"-X", "-W", "--check-hash-based-pycs"}


# Reserved words that put the next word in command position (bash(1), SHELL GRAMMAR):
# `if true; then <cmd>`, `while …; do <cmd>`, `{ <cmd>; }`, `! <cmd>`.
_CONTROL_WORDS = {"if", "then", "elif", "else", "while", "until", "do", "{", "!"}


def _substitutions(cmd: str) -> list[str]:
    """The bodies of the command substitutions in `cmd`, `$(…)` and backticks, also
    inside double quotes (where they still run), never inside single quotes or a
    quoted heredoc's body (where they are text). Nested ones are read by the caller's
    recursion on each body."""
    inert = [(r.start, r.end) for r in inert_regions(cmd) if r.kind in ("single", "comment", "heredoc")]
    out: list[str] = []
    i, n = 0, len(cmd)
    while i < n:
        span = next(((a, b) for a, b in inert if a <= i < b), None)
        if span:
            i = span[1]
            continue
        if cmd[i] == "\\":
            i += 2
            continue
        if cmd.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(cmd[j], 0)
                j += 1
            out.append(cmd[i + 2:j - 1 if not depth else j])
            i = j
            continue
        if cmd[i] == "`":
            j = cmd.find("`", i + 1)
            j = n if j == -1 else j
            out.append(cmd[i + 1:j])
            i = j + 1
            continue
        i += 1
    return out


def _command_word(words: list[str], gate, names: list[str] | None = None) -> int:
    """The index of the program a simple command runs: past assignments, the gate's
    wrappers that run their command once (env, command, builtin, exec, nohup, nice,
    time, sudo, timeout), xargs, and a shell running a wrapper script whose arguments
    are the command (`bash scripts/wt.sh <command>`). `names` collects the basename of
    every program met in command position on the way, the wrappers' and the last."""
    i = 0
    while i < len(words):
        word, name = words[i], os.path.basename(words[i])
        if names is not None and not gate._ASSIGNMENT.match(word):
            names.append(name)
        if gate._ASSIGNMENT.match(word) or word in _CONTROL_WORDS:
            i += 1
        elif name in gate._ONCE_WRAPPERS:
            takes = gate._ONCE_WRAPPERS[name]
            i += 1
            while i < len(words) and (words[i].startswith("-") or gate._ASSIGNMENT.match(words[i])):
                i += 2 if words[i] in takes else 1
            if name == "timeout" and i < len(words):
                i += 1  # the duration
        elif name == "xargs":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in _XARGS_WITH_VALUE else 1
        elif name in gate._SHELLS and gate._shell_run(words, i) is None:
            i += 1  # a shell given a script operand: the words after the script
            while i < len(words) and words[i][:1] in "-+" and words[i] != "--":
                i += 1 + sum(words[i][1:].count(o) for o in "oO")  # `-o pipefail`
            i += 2 if i < len(words) and words[i] == "--" else 1
        else:
            break
    return i


def _await_args(words: list[str], i: int) -> list[str] | None:
    """The words after `await` when the program at `i` runs release-run.py's `await`:
    the script itself (`./release-run.py await`), or the script file a Python
    interpreter there runs. Elsewhere among a program's arguments the name is text."""
    k = i
    if k < len(words) and _PYTHON.match(os.path.basename(words[k])):
        k += 1
        while k < len(words) and words[k].startswith("-"):
            if words[k] in ("-c", "-m"):
                return None
            if words[k] == "--":
                k += 1
                break
            k += 2 if words[k] in _PYTHON_WITH_VALUE else 1
    if k + 1 < len(words) and os.path.basename(words[k]) == "release-run.py" and words[k + 1] == "await":
        return words[k + 2:]
    return None


def _join_continuations(cmd: str) -> str:
    """`cmd` with every backslash-newline pair removed where Bash removes it (bash(1),
    QUOTING): outside single quotes and comments, inside double quotes too. Done before
    any other reading, so `aw\\<newline>ait` reads `await` as Bash runs it."""
    out: list[str] = []
    i, n, quote = 0, len(cmd), None
    while i < n:
        ch = cmd[i]
        if quote == "'":
            out.append(ch)
            quote = None if ch == "'" else quote
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if cmd[i + 1] != "\n":
                out.append(cmd[i:i + 2])
            i += 2
            continue
        if quote is None and ch == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            j = cmd.find("\n", i)
            j = n if j == -1 else j
            out.append(cmd[i:j])
            i = j
            continue
        if ch in "'\"" and quote in (None, ch):
            quote = None if quote else ch
        out.append(ch)
        i += 1
    return "".join(out)


class _Text:
    """One text the shell executes, read as the gate reads it: the simple commands'
    words (quotes and escapes resolved, redirections removed) and the shell tokens
    (quoted text inside its word, `&`, `|`, `;` and parentheses as operators). Comments
    and heredoc bodies are no words; a heredoc a shell reads is a text of its own."""

    def __init__(self, text: str, gate):
        self.text = text
        masked = mask_inert(text)
        self.code = mask_inert(text, keep=lambda _c, region: region.kind in ("single", "double"))
        self.commands: list[tuple[int, int, list[str]]] = []
        for start, end, _sep in simple_commands(masked):
            raw = self.code[start:end]
            if not raw.strip():
                continue
            try:
                self.commands.append((start, end, shlex.split(gate._strip_redirects(raw), posix=True)))
            except (ValueError, gate.Ambiguous):
                continue  # unreadable here; release-run.py refuses a slice-less await itself

    def operators(self) -> list[str] | None:
        """The shell's operator tokens, or None when the text cannot be split."""
        lexer = shlex.shlex(self.code, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            return None
        return [t for t in tokens if t and all(c in lexer.punctuation_chars for c in t)]


def _executable_texts(cmd: str) -> list[_Text]:
    """`cmd` and every text it hands to a shell to run: each command substitution's
    body, `eval`'s words, the `-c` string or heredoc of a shell in command position;
    recursively. Text among another program's arguments is not one."""
    gate = _gate()
    top = _Text(cmd, gate)
    texts = [top]
    for body in _substitutions(cmd):
        texts += _executable_texts(body)
    for start, end, words in top.commands:
        i = _command_word(words, gate)
        if i < len(words) and words[i] == "eval":
            texts += _executable_texts(" ".join(words[i + 1:]))
            continue
        shell = gate._shell_run(words, i) if i < len(words) else None
        if shell is None:
            continue
        inner = [shell[1]] if shell[1] is not None else [
            cmd[r.start:r.end] for r in inert_regions(cmd) if r.kind == "heredoc" and start <= r.anchor < end]
        for text in inner:
            texts += _executable_texts(text)
    return texts


def _release_awaits(texts: list[_Text]) -> list[list[str]]:
    """The words after `await` of every `release-run.py await` these texts run, read
    for the foreground's `--slice-min` check.

    Only the command word counts: an await is release-run.py run there (behind
    wrappers and control-flow words), or the script a Python interpreter there runs.
    Text among another program's arguments (`echo bash -c '…'`, `grep -e release-run.py
    await`), a commit message or a heredoc written to a file only mentions the command.
    What this cannot read (a path held in a variable, `python -m`) release-run.py
    refuses itself: it exits 2 on an await without `--slice-min`."""
    gate = _gate()
    found: list[list[str]] = []
    for text in texts:
        for _start, _end, words in text.commands:
            args = _await_args(words, _command_word(words, gate))
            if args is not None:
                found.append(args)
    return found


# A command that names the script and `await` anywhere in its de-quoted text, inside
# substitutions, `-c` strings, heredocs and control-flow bodies alike: what the
# background check reads, failing closed (a mere mention counts).
_NAMES_AWAIT = (re.compile(r"release[-_]run"), re.compile(r"(?<![\w-])await(?![\w-])"))
# A lone `&` in an operator token, the job-control operator: not `&&`, not a
# redirection (`2>&1`, `&>f`, `>&2`), not the `|&` pipe.
_LONE_AMPERSAND = re.compile(r"(?<![&|<>])&(?![&>])")
# Programs that detach their command from the call (setsid(1) forks when the caller
# leads its process group), and the shell's own job words, matched by the basename of
# a program in command position (`/usr/bin/setsid`, `env X=1 setsid`).
_DETACHERS = {"setsid", "disown", "coproc", "daemonize", "screen", "tmux"}


def _job_control(texts: list[_Text]) -> str | None:
    """The job-control operator or detaching program one of these texts runs, or None."""
    gate = _gate()
    for text in texts:
        operators = text.operators()
        if operators is None:  # unbalanced quotes: read the raw text, failing closed
            raw = _LONE_AMPERSAND.search(text.code)
            if raw:
                return "&"
        for token in operators or []:
            if _LONE_AMPERSAND.search(token):
                return "&"
        for _start, _end, words in text.commands:
            names: list[str] = []
            _command_word(words, gate, names)
            hit = next((n for n in names if n in _DETACHERS), None)
            if hit:
                return hit
    return None


def release_wait(cmd: str, background: bool) -> str | None:
    """Rule 3. The reason a release verdict would be read where it can be lost, or None.
    Advisory: see the module docstring for the contract release-run.py holds itself."""
    if not all(rx.search(_plain(cmd)) for rx in _NAMES_AWAIT):
        return None
    if background:
        return ("it reads a release run's verdict in the background (it names release-run and `await` "
                "with `run_in_background`; a mere mention is refused there too, so write it in a "
                "foreground call). A background shell dies with the session: on 2026-10-20 the await of "
                "run 39507708450 was killed 7 s after it started, when its T3 thread settled, and the "
                "run's failure went unseen for nine hours")
    texts = _executable_texts(cmd)
    job = _job_control(texts)
    if job:
        return (f"it starts a release run's await as a background job (`{job}`): the call returns at "
                "once, and the verdict is read where it can be lost")
    for args in _release_awaits(texts):
        if not any(a == "--slice-min" or a.startswith("--slice-min=") for a in args):
            return ("`release-run.py await` has no `--slice-min`, so it waits for the run's whole "
                    "deadline in one call, and the Bash tool stops a foreground command at 10 minutes")
    return None


def decide(payload: dict) -> str | None:
    tool_input = payload.get("tool_input") or {}
    # Bash removes line continuations before it reads words; so does every rule here.
    cmd = _join_continuations(str(tool_input.get("command") or ""))
    why = release_wait(cmd, bool(tool_input.get("run_in_background")))
    if why:
        return (f"Refused before running: {why}. Run it in the foreground, with no `&`, with `--slice-min "
                f"9` and the Bash tool's `timeout` at 600000; exit 6 means the slice ended and the run goes "
                f"on, so run the same command again until it prints the verdict. Nothing ran.")
    if not cmd or not any(w in cmd for w in ("sleep", "watch", "|")):
        return None
    if not tool_input.get("run_in_background"):
        why = foreground_wait(cmd)
        if why:
            return (f"Refused before running: {why}. A foreground wait holds the session and dies on "
                    f"the Bash timeout, and polling fills the context with the same output. Start the "
                    f"long work, or the wait loop itself, with `run_in_background: true` and act on "
                    f"its completion notification instead of polling its output. A single settle "
                    f"pause of at most {SETTLE_BOUND_S:g} s after a signal you sent (kill, cancel, "
                    f"restart) is allowed. Nothing ran.")
    why = masked_status(cmd)
    if why:
        return (f"Refused before running: {why}, so a failure would read as success. Write the "
                f"output to a file and test the command's own status (`cmd > /tmp/x.log 2>&1; echo "
                f"exit=$?`, then read the file), or put `set -o pipefail` first. Nothing ran.")
    return None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("tool_name") != "Bash":
            return
        reason = decide(payload)
    except Exception:  # a habit guard never blocks work by crashing
        return
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))


if __name__ == "__main__":
    main()
