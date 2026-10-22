"""The agent's own GitHub identity, and the owner's review as an approval.

Owner, 2026-10-22: the agents push branches and open pull requests as a GitHub App,
so the owner can review them on GitHub (GitHub refuses a review of your own pull
request, and every PR used to be authored by the owner's own account). The owner
then approves a change by approving its pull request, after watching its evidence.

The machine opts in with a config file outside every repository, readable only by
its owner (mode 600):

    ~/.config/claude-tdd-workflow/github-app.json   (or $CLAUDE_TDD_GITHUB_APP_CONFIG)
    {
      "appId": 123456,                 # or "clientId": "Iv23..." (the JWT's issuer)
      "installationId": 7890123,
      "privateKeyPath": "~/.config/claude-tdd-workflow/agent-app.private-key.pem",
      "botLogin": "endoxia-agent[bot]",
      "ownerLogin": "mqlstam",
      "repositories": ["EndoxiaBV/Endoxia"]
    }

Without the file everything works as before: the owner's own identity, and a
release only after a typed approval.

A review approves a merge only when, read at that moment from GitHub: the PR was
opened by `botLogin` (so the owner did not review their own work), and the owner's
newest submitted review is APPROVED on exactly the PR's current head. Every newer
review by the owner counts against it, a COMMENTED one included (GitHub keeps the
approval then; this gate does not, because a comment after an approval is something
the owner wants worked), and so does a dismissal (GitHub rewrites the review's state
to DISMISSED) and any commit pushed after the approval (the review names the commit
it approved).

    python3 agent_app.py review <PR> [-R owner/repo]   # what the owner's review says
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from typing import NamedTuple

CONFIG_ENV = "CLAUDE_TDD_GITHUB_APP_CONFIG"
DEFAULT_CONFIG = "~/.config/claude-tdd-workflow/github-app.json"
REQUIRED = ("installationId", "privateKeyPath", "botLogin", "ownerLogin", "repositories")

# The longest one `gh api` read may take. gh reads measured 2026-10-20 from this
# machine took 0.4-0.9 s (release-run.py); 30 s is over 30x the slowest.
GH_CALL_TIMEOUT_S = 30


class ConfigError(Exception):
    """The config exists but cannot be used: refused out loud, never ignored."""


class AppConfig(NamedTuple):
    issuer: str
    installation_id: str
    private_key_path: str
    bot_login: str
    owner_login: str
    repositories: frozenset[str]

    def covers(self, repo: str | None) -> bool:
        return bool(repo) and repo.lower() in {r.lower() for r in self.repositories}


def config_path() -> str:
    return os.path.expanduser(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)


def load_config(path: str | None = None) -> AppConfig | None:
    """The App config, None when this machine has not opted in."""
    path = path or config_path()
    if not os.path.exists(path):
        return None
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path} is readable by others (mode {stat.S_IMODE(mode):o}); chmod 600 it")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as error:
        raise ConfigError(f"{path} is unreadable ({error})") from error
    missing = [key for key in REQUIRED if not data.get(key)]
    issuer = data.get("clientId") or data.get("appId")
    if not issuer:
        missing.append("appId or clientId")
    if missing:
        raise ConfigError(f"{path} lacks {', '.join(missing)}")
    repos = data["repositories"]
    if not isinstance(repos, list) or not all(isinstance(r, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", r)
                                              for r in repos):
        raise ConfigError(f"{path}: repositories must be a list of owner/repo")
    return AppConfig(str(issuer), str(data["installationId"]),
                     os.path.expanduser(str(data["privateKeyPath"])), str(data["botLogin"]),
                     str(data["ownerLogin"]), frozenset(repos))


class Review(NamedTuple):
    approved: bool
    head: str | None
    reason: str


def judge_review(pr: dict, reviews: list[dict], config: AppConfig) -> Review:
    """Whether the owner's review approves the PR's current head. `pr` and `reviews`
    are GitHub's REST answers (`GET /repos/{o}/{r}/pulls/{n}` and its `/reviews`,
    oldest review first)."""
    head = str(((pr.get("head") or {}).get("sha")) or "") or None
    author = str((pr.get("user") or {}).get("login") or "")
    if author.lower() != config.bot_login.lower():
        return Review(False, head, f"the PR was opened by {author or 'an unknown account'}, not by the agent "
                                   f"App ({config.bot_login}), so a review on GitHub does not approve it; "
                                   f"type `/approve ship`")
    own = [r for r in reviews if str((r.get("user") or {}).get("login") or "").lower()
           == config.owner_login.lower() and str(r.get("state") or "").upper() != "PENDING"]
    if not own:
        return Review(False, head, f"{config.owner_login} has not reviewed the PR")
    last = own[-1]
    state = str(last.get("state") or "").upper()
    if state != "APPROVED":
        return Review(False, head, f"{config.owner_login}'s newest review is {state}, not APPROVED")
    approved = str(last.get("commit_id") or "")
    if not head or approved != head:
        return Review(False, head, f"{config.owner_login} approved {approved[:12] or 'another commit'}, "
                                   f"and the head is now {(head or '?')[:12]}: a commit came after the approval")
    return Review(True, head, f"{config.owner_login} approved the head {head[:12]}")


def _gh_json_lines(run, argv: list[str], env: dict | None = None):
    out = run(argv, capture_output=True, text=True, timeout=GH_CALL_TIMEOUT_S, env=env)
    if out.returncode != 0:
        return None
    try:
        return [json.loads(line) for line in (out.stdout or "").splitlines() if line.strip()]
    except ValueError:
        return None


def read_review(pr: str, repo: str, config: AppConfig, run=subprocess.run, env: dict | None = None) -> Review | None:
    """The owner's review of PR `pr` in `repo` as GitHub says now; None when it cannot be read."""
    if not re.fullmatch(r"\d+", pr or "") or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        return None
    try:
        head = _gh_json_lines(run, ["gh", "api", f"repos/{repo}/pulls/{pr}", "--jq",
                                    "{user: {login: .user.login}, head: {sha: .head.sha}, state: .state}"], env)
        reviews = _gh_json_lines(run, ["gh", "api", "--paginate", f"repos/{repo}/pulls/{pr}/reviews", "--jq",
                                       ".[] | {user: {login: .user.login}, state: .state, commit_id: .commit_id}"],
                                 env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not head or reviews is None:
        return None
    return judge_review(head[0], reviews, config)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 4) or argv[0] != "review" or (len(argv) == 4 and argv[2] not in ("-R", "--repo")):
        print("usage: agent_app.py review <PR> [-R owner/repo]", file=sys.stderr)
        return 2
    try:
        config = load_config()
    except ConfigError as error:
        print(f"refused: {error}")
        return 1
    if config is None:
        print(f"no agent App on this machine ({config_path()} is absent): the owner approves with `/approve ship`")
        return 1
    if len(argv) == 4:
        repo = argv[3]
    else:
        out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                             capture_output=True, text=True, timeout=GH_CALL_TIMEOUT_S)
        repo = out.stdout.strip() if out.returncode == 0 else ""
    review = read_review(argv[1], repo, config)
    if review is None:
        print(f"the review of PR {argv[1]} in {repo or '?'} could not be read from GitHub")
        return 1
    print(("APPROVED: " if review.approved else "NOT APPROVED: ") + review.reason)
    return 0 if review.approved else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
