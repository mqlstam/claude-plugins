#!/usr/bin/env python3
"""Record and check the VERIFY verdict against the exact contents that were reviewed.

VERIFY used to record only `git rev-parse HEAD`. A verdict reached on uncommitted
work then still matched after anyone changed that work without committing, so
/ship could commit code no reviewer had read. The verdict is now bound to the
working tree's CONTENTS: every tracked and every untracked, non-ignored file,
by mode and git object id, and every submodule by the commit it has checked
out. Committing reviewed work leaves the contents, and so the verdict,
unchanged; editing, adding or deleting any file does not.

    verify-state.py record                 # VERIFY reached READY
    verify-state.py check                  # before staging: the working tree
    verify-state.py check --commit <rev>   # after committing, and before pushing
    verify-state.py adopt-squash           # right before merging main in
    verify-state.py integrate              # right after merging main in

The working-tree check alone is not enough: an index staged before the review
can commit other contents than the files on disk. So the release skills check
the working tree before they stage and the commit before they push.

Merging main in is not a review event (a clean integration does not re-arm
VERIFY), but it changes the contents. `integrate` accepts the merge commit's
tree only when it equals what git's own clean merge of the two parents
produces (`git merge-tree --write-tree`) and the first parent is covered; a
conflict resolved by hand, or anything a hook changed, is named for a targeted
review instead. Accepted trees are kept with the verdict, so a release resumed
later, or /merge run from another checkout of the same repository, still finds
the commit covered.

A branch that keeps going after one of its PRs was squash-merged shares no
history with that squash commit, so merging main in replays the PR's own changes
against the branch's later edits of the same lines. Endoxia PR #1422 met 82
conflicted paths that way (its branch's #1397 was on main as a squash); 80 of
them were the branch against itself. `adopt-squash` records such a squash commit
as merged first (`git merge -s ours`), but only when the PR's head is in this
branch's history and the squash commit has exactly that head's tree, so the
adoption changes no file. The merge of main then starts from the squash commit:
replayed on #1422, it leaves the 2 paths both sides really changed, and the other
80 merge to exactly what was resolved by hand. `integrate` accepts the result as
before, because the adoption keeps the covered tree.

Each check exits 0 when a READY verdict covers exactly those contents, and 1
otherwise, naming every path that differs.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = ".claude/.verify-state.json"
FORMAT = 5
MAIN_REF = "refs/remotes/origin/main"
GITLINK = "160000"

# The longest the `gh` call of adopt-squash may take. `gh` reads measured 2026-10-20
# from this machine took 0.4-0.9 s (release-run.py); 30 s is over 30x the slowest. At
# the bound nothing is adopted and the merge of main goes on as before.
GH_CALL_TIMEOUT_S = 30

# How many of the branch's merged PRs adopt-squash reads. `gh pr list` returns the
# newest first. The most merged PRs on one head branch of EndoxiaBV/Endoxia is 34
# (`worktree-melodic-tinkering-orbit`, of 1,272 merged PRs read 2026-10-21); 100 is
# about three times that. A full page is named in the output.
MERGED_PR_LIMIT = 100


class Refusal(Exception):
    """The contents cannot be bound to a verdict as they stand."""


def git(root, *args, stdin=None, check=True):
    result = subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout


def toplevel():
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())


# --- the working tree's contents ------------------------------------------------

def changed_paths(root):
    """Paths whose working-tree content may differ from the index, untracked included."""
    tokens = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    paths = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        status, path = token[:2], token[3:].decode()
        paths.add(path.rstrip("/"))
        if status[:1] in (b"R", b"C"):
            # With -z a rename or copy is followed by its source path.
            paths.add(tokens[index].decode())
            index += 1
    return paths


def refuse_masked_entries(root):
    """assume-unchanged and skip-worktree hide a file on disk from `git status`."""
    masked = []
    for record in git(root, "ls-files", "-v", "-z").split(b"\0"):
        if not record:
            continue
        tag, path = record[:1].decode(), record[2:].decode()
        if (tag.islower() or tag == "S") and (root / path).exists():
            masked.append(path)
    if masked:
        raise Refusal(
            "these files carry assume-unchanged or skip-worktree, so their contents on disk "
            "cannot be read reliably; clear the flag (git update-index --no-assume-unchanged / "
            "--no-skip-worktree): " + ", ".join(sorted(masked))
        )


def submodule_entry(root, path, recorded_oid):
    """A submodule is bound to the commit it has checked out, and only when clean."""
    full = root / path
    if not (full / ".git").exists():
        # Not initialized: nothing of it is on disk, so the gitlink is its content.
        return GITLINK, recorded_oid
    if git(full, "status", "--porcelain", "--untracked-files=all").strip():
        raise Refusal(f"submodule {path} has uncommitted changes; commit them inside it first")
    return GITLINK, git(full, "rev-parse", "HEAD").decode().strip()


def working_entry(root, path, index_modes, index_oids):
    """The (mode, object id) a commit of this path would record now, or None when gone."""
    full = root / path
    if index_modes.get(path) == GITLINK or (full.is_dir() and (full / ".git").exists()):
        return submodule_entry(root, path, index_oids.get(path, ""))
    if full.is_symlink():
        # The link's target text, hashed by git in the repository's object format.
        return "120000", git(root, "hash-object", "--stdin", stdin=os.readlink(full).encode()).decode().strip()
    if not full.is_file():
        return None
    mode = "100755" if os.access(full, os.X_OK) else "100644"
    # hash-object applies the repository's clean filters, as a commit would.
    return mode, git(root, "hash-object", "--", path).decode().strip()


def index_contents(root):
    contents, modes = {}, {}
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, path = record.decode().split("\t", 1)
        mode, blob, _stage = meta.split(" ")
        contents[path] = f"{mode} {blob}"
        modes[path] = mode
    return contents, modes


def tree_contents(root):
    """{path: "mode id"} for every file a commit of the working tree would contain."""
    refuse_masked_entries(root)
    contents, modes = index_contents(root)
    oids = {path: entry.split(" ")[1] for path, entry in contents.items()}
    for path, mode in modes.items():
        if mode == GITLINK:
            contents[path] = " ".join(submodule_entry(root, path, oids[path]))
    for path in changed_paths(root):
        entry = working_entry(root, path, modes, oids)
        if entry is None:
            contents.pop(path, None)
        else:
            contents[path] = " ".join(entry)
    contents.pop(STATE_PATH, None)
    return contents


def commit_contents(root, rev):
    """{path: "mode id"} of a commit's tree, recursively."""
    contents = {}
    for record in git(root, "ls-tree", "-r", "-z", "--full-tree", rev).split(b"\0"):
        if not record:
            continue
        meta, path = record.decode().split("\t", 1)
        mode, _kind, oid = meta.split(" ")
        contents[path] = f"{mode} {oid}"
    contents.pop(STATE_PATH, None)
    return contents


def tree_of(root, rev):
    return git(root, "rev-parse", f"{rev}^{{tree}}").decode().strip()


def digest(contents):
    lines = "".join(f"{entry} {path}\n" for path, entry in sorted(contents.items()))
    return hashlib.sha256(lines.encode()).hexdigest()


def differences(current, recorded):
    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    changed = sorted(p for p in set(current) & set(recorded) if current[p] != recorded[p])
    return [("added", p) for p in added] + [("deleted", p) for p in removed] + [("modified", p) for p in changed]


# --- the verdict ------------------------------------------------------------------

def read_state(path):
    """The READY state at this path, or a reason it cannot be used."""
    if not path.is_file():
        return None, f"no VERIFY verdict: {STATE_PATH} is missing; complete VERIFY first"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        return None, f"the VERIFY verdict at {path} is unreadable ({error}); run VERIFY again"
    if state.get("verdict") != "READY":
        return None, f"the recorded VERIFY verdict is {state.get('verdict')!r}, not READY"
    if state.get("format") != FORMAT:
        return None, "the recorded VERIFY verdict predates the current format; run VERIFY again"
    return state, None


def write_state(root, state):
    target = root / STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so an interrupted write never leaves a truncated verdict.
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    os.replace(partial, target)


def covers_commit(root, state, rev):
    """True when the verdict covers the tree of this commit."""
    if tree_of(root, rev) in {entry["tree"] for entry in state.get("accepted", [])}:
        return True
    return commit_contents(root, rev) == state["contents"]


def worktree_states(root):
    """The verdict files of every checkout of this repository, this one first."""
    paths = [root / STATE_PATH]
    for line in git(root, "worktree", "list", "--porcelain").decode().splitlines():
        if line.startswith("worktree "):
            candidate = Path(line[len("worktree "):]) / STATE_PATH
            if candidate not in paths:
                paths.append(candidate)
    return paths


def record(root):
    contents = tree_contents(root)
    state = {
        "format": FORMAT,
        "verdict": "READY",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "headSha": git(root, "rev-parse", "HEAD").decode().strip(),
        "treeDigest": digest(contents),
        "contents": contents,
        "accepted": [],
    }
    write_state(root, state)
    print(f"VERIFY READY recorded for {len(contents)} files, digest {state['treeDigest'][:12]}")
    return 0


def check_working_tree(root):
    state, reason = read_state(root / STATE_PATH)
    if state is None:
        print(reason)
        return 1
    current = tree_contents(root)
    if current == state["contents"]:
        print(f"VERIFY READY covers the working tree ({len(current)} files)")
        return 0
    clean = not changed_paths(root) - {STATE_PATH}
    if clean and covers_commit(root, state, "HEAD"):
        print("VERIFY READY covers the working tree: a clean checkout of an accepted integration")
        return 0
    print("the working tree differs from what VERIFY reviewed; review these paths again:")
    for label, path in differences(current, state["contents"]):
        print(f"  {label}: {path}")
    return 1


def check_commit(root, rev):
    if git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False).strip() == b"":
        print(f"commit {rev} is not available in this repository; fetch it first")
        return 1
    reasons = []
    for path in worktree_states(root):
        state, reason = read_state(path)
        if state is None:
            if path == root / STATE_PATH:
                reasons.append(reason)
            continue
        if covers_commit(root, state, rev):
            where = "" if path == root / STATE_PATH else f" (verdict from {path.parent.parent})"
            print(f"VERIFY READY covers commit {rev}{where}")
            return 0
    own, _ = read_state(root / STATE_PATH)
    if own is None:
        print(reasons[0] if reasons else "no VERIFY verdict in any checkout covers this commit")
        return 1
    print(f"commit {rev} differs from what VERIFY reviewed; review these paths again:")
    for label, path in differences(commit_contents(root, rev), own["contents"]):
        print(f"  {label}: {path}")
    return 1


def integrate(root):
    """Accept HEAD, a merge of main into covered work, when it is exactly git's clean merge."""
    state, reason = read_state(root / STATE_PATH)
    if state is None:
        print(reason)
        return 1
    if covers_commit(root, state, "HEAD"):
        # `git merge` answered "Already up to date", or the merge itself was reviewed.
        print("nothing to integrate: the verdict already covers HEAD")
        return 0
    parents = git(root, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()[1:]
    if len(parents) != 2:
        print("HEAD is not a merge of two parents; integrate runs right after merging main in")
        return 1
    ours, theirs = parents
    if not covers_commit(root, state, ours):
        print(f"the merge's first parent {ours[:12]} is not covered by the verdict; check it first")
        return 1
    # Only main's own history is exempt from review; anything else merged in is new
    # code. "Of main" means an ancestor of the fetched tip, not the tip itself: the
    # remote-tracking ref is shared by every worktree, and another session's fetch
    # may have moved it on since this merge was made.
    of_main = subprocess.run(
        ["git", "merge-base", "--is-ancestor", theirs, MAIN_REF], cwd=root, capture_output=True
    ).returncode == 0
    if not of_main:
        print(f"the merged-in parent {theirs[:12]} is not part of {MAIN_REF};")
        print("only an integration of the fetched main is accepted. Review what it brings in, then `record`.")
        return 1
    # Exit 1 means conflicts; with --name-only --no-messages the output is the
    # merged tree id followed by one conflicted path per line.
    merge = subprocess.run(
        ["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs],
        cwd=root, capture_output=True,
    )
    if merge.returncode not in (0, 1):
        raise subprocess.CalledProcessError(merge.returncode, merge.args, merge.stdout, merge.stderr)
    lines = merge.stdout.decode().splitlines()
    expected = lines[0].strip()
    conflicted = [line for line in lines[1:] if line] if merge.returncode == 1 else []
    actual = tree_of(root, "HEAD")
    if merge.returncode == 1:
        # The resolution may also have changed files that did not conflict; every
        # difference from git's own merge result is part of what needs review.
        edited = {
            line.split("\t", 1)[1]
            for line in git(root, "diff", "--name-status", "--no-renames", expected, actual).decode().splitlines()
        }
        print("the merge had conflicts; give these paths a targeted correctness review,")
        print("then run `verify-state.py record` once it is READY:")
        for path in sorted(set(conflicted) | edited):
            label = "conflicted" if path in conflicted else "edited in the resolution"
            print(f"  {label}: {path}")
        return 1
    if actual != expected:
        print("the merge commit differs from git's clean merge of its parents (a hand edit or")
        print("a hook); give these paths a targeted correctness review, then `record`:")
        for line in git(root, "diff", "--name-status", "--no-renames", expected, actual).decode().splitlines():
            status, path = line.split("\t", 1)
            print(f"  {status}: {path}")
        return 1
    state.setdefault("accepted", []).append({"tree": actual, "from": ours, "main": theirs})
    write_state(root, state)
    print(f"accepted the clean integration of {theirs[:12]} into covered {ours[:12]}")
    return 0


# --- adopting this branch's own squash-merged PRs ---------------------------------

def is_ancestor(root, ancestor, rev):
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, rev],
                          cwd=root, capture_output=True).returncode == 0


def is_commit(root, oid):
    return subprocess.run(["git", "cat-file", "-e", f"{oid}^{{commit}}"], cwd=root,
                          capture_output=True).returncode == 0


def merged_prs(root, branch):
    """This branch's merged PRs, latest merge first, as (number, head, squash), and
    a note; or None and the reason they could not be read."""
    command = ["gh", "pr", "list", "--head", branch, "--state", "merged", "--limit", str(MERGED_PR_LIMIT),
               "--json", "number,headRefOid,mergeCommit,mergedAt"]
    try:
        out = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=GH_CALL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"`gh pr list` did not answer ({error.__class__.__name__})"
    if out.returncode != 0:
        return None, f"`gh pr list` failed: {(out.stderr.strip().splitlines() or ['no output'])[-1]}"
    try:
        rows = json.loads(out.stdout)
    except ValueError:
        return None, "`gh pr list` printed no JSON"
    prs = sorted(
        ((str(row.get("mergedAt") or ""), int(row["number"]), str(row["headRefOid"]),
          str((row.get("mergeCommit") or {}).get("oid") or ""))
         for row in rows if row.get("number") and row.get("headRefOid")),
        reverse=True,
    )
    note = (f"read the newest {MERGED_PR_LIMIT} merged PRs of {branch}; an older one is not considered"
            if len(rows) >= MERGED_PR_LIMIT else None)
    return [(number, head, squash) for _merged, number, head, squash in prs if squash], note


def adopt_squash(root):
    """Record this branch's own squash-merged PRs as merged, so merging main in starts
    from them instead of replaying their changes against this branch's later work."""
    branch = git(root, "branch", "--show-current").decode().strip()
    if not branch:
        print("refused: HEAD is detached; adopt-squash runs on the branch being shipped")
        return 1
    if not is_commit(root, MAIN_REF):
        print(f"refused: {MAIN_REF} is missing; `git fetch origin main` first")
        return 1
    if is_ancestor(root, "HEAD", MAIN_REF):
        print(f"adopted nothing: {branch} is already part of the fetched main")
        return 0
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0:
        print("refused: changes are staged; commit them first, so that the adoption changes no file")
        return 1
    prs, note = merged_prs(root, branch)
    if prs is None:
        print(f"adopted nothing: {note}. Merging main in may then report this branch's merged "
              "changes as conflicts")
        return 0
    if note:
        print(note)
    adopted = reported = 0
    for number, head, squash in prs:
        if not is_commit(root, squash):
            print(f"#{number} not adopted: its squash commit {squash[:12]} is not in this clone; "
                  "`git fetch origin main` first")
            reported += 1
            continue
        if is_ancestor(root, squash, "HEAD"):
            continue  # already part of this branch
        if not is_ancestor(root, squash, MAIN_REF):
            print(f"#{number} not adopted: its merge commit {squash[:12]} is not part of {MAIN_REF}")
            reported += 1
            continue
        if not is_commit(root, head) or not is_ancestor(root, head, "HEAD"):
            print(f"#{number} not adopted: its head {head[:12]} is not in this branch's history")
            reported += 1
            continue
        if tree_of(root, head) != tree_of(root, squash):
            print(f"#{number} not adopted: main's squash commit {squash[:12]} does not have the tree of "
                  f"the PR's head {head[:12]} (main moved after the head's last integration), so "
                  "recording it as merged could drop main's changes. Merging main in replays the PR's "
                  "changes; conflicts in the files it changed are this branch against itself")
            reported += 1
            continue
        before, tree = git(root, "rev-parse", "HEAD").decode().strip(), tree_of(root, "HEAD")
        merge = subprocess.run(["git", "merge", "--no-ff", "-s", "ours", "--no-edit",
                                "-m", f"chore: adopt squash-merged #{number}", squash],
                               cwd=root, capture_output=True)
        if merge.returncode != 0:
            detail = (merge.stderr or merge.stdout).decode(errors="replace").strip()
            print(f"refused: `git merge -s ours {squash[:12]}` failed: {detail}")
            return 1
        parents = git(root, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()[1:]
        if parents != [before, squash] or tree_of(root, "HEAD") != tree:
            print(f"refused: the adoption commit of #{number} is not a merge of {before[:12]} and "
                  f"{squash[:12]} with an unchanged tree (a hook?); read `git show --stat HEAD`")
            return 1
        print(f"adopted #{number}: main's squash commit {squash[:12]} has exactly the tree of this "
              f"branch's {head[:12]}, so it is recorded as merged and no file changes")
        adopted += 1
    if not adopted and not reported:
        print(f"adopted nothing: no merged PR of {branch} is missing from it"
              if prs else f"adopted nothing: no PR with {branch} as its head was merged")
    return 0


def main(argv):
    args = argv[1:]
    try:
        root = toplevel()
        if args == ["record"]:
            return record(root)
        if args == ["check"]:
            return check_working_tree(root)
        if len(args) == 3 and args[:2] == ["check", "--commit"]:
            return check_commit(root, args[2])
        if args == ["integrate"]:
            return integrate(root)
        if args == ["adopt-squash"]:
            return adopt_squash(root)
    except Refusal as refusal:
        print(f"refused: {refusal}")
        return 1
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode(errors="replace").strip()
        print(f"refused: `{' '.join(error.cmd)}` failed: {detail}")
        return 1
    print("usage: verify-state.py record | check [--commit <rev>] | adopt-squash | integrate", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
