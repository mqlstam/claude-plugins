"""Shell text that is not a command: comments, quoted strings and heredoc bodies.

Shared by the plugin's Bash hooks (release-approval-gate.py, bash-shape-guard.py).
Both read a command's TEXT to decide what it does, and both must not read
`echo "... gh pr merge ..."`, a commit message or a PR body as a command. One
reads every inert region as inert; the other needs some of them back (the
string handed to `bash -c` or `ssh host '...'` IS a command), so the scanner
returns the regions and the caller decides which to blank.
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple


class Region(NamedTuple):
    kind: str  # "comment" | "single" | "double" | "heredoc"
    start: int  # first blanked offset
    end: int  # one past the last blanked offset
    anchor: int  # where the construct begins: the quote, the `#`, or the `<<`


def inert_regions(cmd: str) -> list[Region]:
    regions: list[Region] = []
    i, n = 0, len(cmd)
    pending_heredocs: list[tuple[str, bool, int]] = []
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            j = cmd.find("\n", i)
            j = n if j == -1 else j
            regions.append(Region("comment", i, j, i))
            i = j
            continue
        if ch == "'":
            j = cmd.find("'", i + 1)
            j = n - 1 if j == -1 else j
            regions.append(Region("single", i + 1, j, i))
            i = j + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and cmd[j] != '"':
                j += 2 if cmd[j] == "\\" else 1
            j = min(j, n - 1)
            regions.append(Region("double", i + 1, j, i))
            i = j + 1
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = re.match(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", cmd[i:])
            if m:
                pending_heredocs.append((m.group(3), m.group(1) == "-", i))
                i += m.end()
                continue
        if ch == "\n" and pending_heredocs:
            j = i + 1
            while pending_heredocs and j <= n:
                word, dash, anchor = pending_heredocs.pop(0)
                body_start = j
                while j < n:
                    line_end = cmd.find("\n", j)
                    line_end = n if line_end == -1 else line_end
                    line = cmd[j:line_end]
                    if (line.strip() if dash else line) == word:
                        regions.append(Region("heredoc", body_start, j, anchor))
                        j = line_end + 1
                        break
                    j = line_end + 1
                else:
                    regions.append(Region("heredoc", body_start, n, anchor))
            i = j
            continue
        i += 1
    return regions


def mask_inert(cmd: str, keep: Callable[[str, Region], bool] | None = None) -> str:
    """Same-length copy of `cmd` with inert regions blanked, so match offsets
    still index the raw string. `keep(cmd, region)` returning True leaves that
    region's text in place."""
    out = list(cmd)
    for region in inert_regions(cmd):
        if keep is not None and keep(cmd, region):
            continue
        for k in range(region.start, region.end):
            if out[k] != "\n" or region.kind != "heredoc":
                out[k] = " "
    return "".join(out)
