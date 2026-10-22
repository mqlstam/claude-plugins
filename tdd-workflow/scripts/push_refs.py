"""Where one `git push` refspec lands on the remote, read the way git reads it.

One resolver for the release gate and the App wrapper (review finding, 2026-10-22:
`git push origin HEAD:heads/main` reached main past both, because each compared
the destination's text with `main`).

git-push(1), <refspec>: `[+]<src>[:<dst>]`; without `:<dst>` the destination is the
source's own name; an empty <src> deletes <dst>. A <dst> that does not start with
`refs/` is resolved against the remote's refs with git's rev-parse rules
(gitrevisions(7), refs.c `ref_rev_parse_rules`): `<name>`, `refs/<name>`,
`refs/tags/<name>`, `refs/heads/<name>`, `refs/remotes/<name>`... So `heads/main` is
`refs/heads/main` and `tags/v1` is `refs/tags/v1`; a bare name is taken as a branch
(it is one unless the remote has a tag of exactly that name, which the gate checks
by the tag's own rules). `HEAD` and `@` (gitrevisions: "@ alone is a shortcut for
HEAD") are the current branch.
"""
from __future__ import annotations


def _resolve(name: str) -> tuple[str, str | None]:
    if name in ("HEAD", "@"):
        return "head", None
    for prefix, kind in (("refs/heads/", "branch"), ("refs/tags/", "tag"), ("heads/", "branch"), ("tags/", "tag")):
        if name.startswith(prefix):
            return kind, name[len(prefix):]
    if name.startswith(("refs/", "remotes/")):
        return "other", name
    return "branch", name


def destination(refspec: str) -> tuple[str, str | None]:
    """("branch", name) | ("tag", name) | ("head", None) | ("other", ref) for one refspec."""
    spec = refspec[1:] if refspec.startswith("+") else refspec
    src, colon, dst = spec.partition(":")
    if not colon or not dst:
        dst = src
    return _resolve(dst)


def is_force(refspec: str) -> bool:
    return refspec.startswith("+")
