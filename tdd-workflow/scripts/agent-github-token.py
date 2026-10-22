#!/usr/bin/env python3
"""Run a branch push or a PR create/edit as the agent's GitHub App (agent_app.py).

    agent-github-token.py run git push -u origin <branch>
    agent-github-token.py run gh pr create --title <t> --body-file <f>
    agent-github-token.py run gh pr edit <PR> --body-file <f>
    agent-github-token.py credential get        # git's credential helper protocol
    agent-github-token.py status                # configured? which bot? token valid until?

Owner, 2026-10-22: the agents open their pull requests as the App so the owner can
review and approve them on GitHub ("Pull request authors cannot approve their own
pull requests"). Without the App config (agent_app.py) `run` runs the command
unchanged, as the user, so the skills have one form either way.

What runs as the App is only what puts work up for review: pushing a feature branch
and creating, editing, reading or commenting on a PR. A merge, a push to main, a
tag, a dispatch, a deletion or a force push is refused here, whoever is configured:
those are releases, and the release gate refuses one run through another program
anyway. `--attach` is refused too: gh uploads attachments only with a user token
(gh `internal/attachments/client.go` accepts gho_/ghp_/github_pat_/ghu_, not an
installation's ghs_), so the evidence is attached with the user's own gh.

The token: a JWT signed with the App's private key (RS256 through `openssl dgst
-sha256 -sign`, no new dependency; iat 60 s in the past against clock drift, exp 9
minutes ahead where GitHub allows at most 10, iss the client or app ID), exchanged
at POST /app/installations/{id}/access_tokens for an installation token that lives
one hour (docs.github.com, read 2026-10-22). It is cached mode 600 and renewed
REFRESH_MARGIN_S before it expires. Neither the key nor the token is ever printed:
gh gets it in its environment, git asks for it through the credential helper.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_app  # noqa: E402

API = "https://api.github.com"
# The version the plugin's other REST calls assume; supported until 2028-03-10
# (docs.github.com/en/rest/about-the-rest-api/api-versions).
API_VERSION = "2022-11-28"
CACHE = "~/.cache/claude-tdd-workflow/github-app-token.json"
# GitHub: "set this 60 seconds in the past", and exp "no more than 10 minutes into
# the future". 540 s leaves a minute of drift on the far side too.
JWT_BACKDATE_S = 60
JWT_LIFETIME_S = 540
# Renew this long before the hour is up: longer than any one push or PR call the
# skills make (seconds) plus the drift allowance above, short of the hour's tail.
REFRESH_MARGIN_S = 600
# The token exchange is one small POST; 0.4-0.9 s measured for gh reads here.
HTTP_TIMEOUT_S = 30

GIT_PUSH_OPTIONS = {"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--no-verify",
                    "--force-with-lease", "--porcelain"}
GH_PR_SUBCOMMANDS = {"create", "edit", "view", "comment", "ready"}
PROTECTED = {"main", "master", "HEAD"}


class TokenError(Exception):
    """Refused or failed; the message names why and never holds the key or the token."""


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(config: agent_app.AppConfig, now: float | None = None) -> str:
    now = int(time.time() if now is None else now)
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iat": now - JWT_BACKDATE_S, "exp": now + JWT_LIFETIME_S, "iss": config.issuer}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    if not os.path.isfile(config.private_key_path):
        raise TokenError(f"the App's private key {config.private_key_path} is missing")
    signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", config.private_key_path],
                            input=f"{header}.{payload}".encode(), capture_output=True)
    if signed.returncode != 0 or not signed.stdout:
        raise TokenError(f"openssl could not sign with the private key {config.private_key_path}")
    return f"{header}.{payload}.{_b64(signed.stdout)}"


def _read_cache(path: str, config: agent_app.AppConfig, now: float) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("installationId") == config.installation_id and \
                _epoch(data["expiresAt"]) - now > REFRESH_MARGIN_S:
            return str(data["token"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no usable cache: mint a new token
    return None


def _write_cache(path: str, config: agent_app.AppConfig, token: str, expires_at: str) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    partial = path + ".partial"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"installationId": config.installation_id, "token": token, "expiresAt": expires_at}, fh)
    os.replace(partial, path)


def installation_token(config: agent_app.AppConfig, cache: str | os.PathLike | None = None, now=time.time,
                       urlopen=urllib.request.urlopen) -> str:
    path = os.path.expanduser(str(cache or CACHE))
    cached = _read_cache(path, config, now())
    if cached:
        return cached
    # Least privilege: the token reaches only the configured repositories.
    names = sorted({repo.split("/", 1)[1] for repo in config.repositories})
    request = urllib.request.Request(
        f"{API}/app/installations/{config.installation_id}/access_tokens", method="POST",
        data=json.dumps({"repositories": names}).encode(),
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {app_jwt(config, now())}",
                 "X-GitHub-Api-Version": API_VERSION, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise TokenError(f"GitHub refused the installation token: HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise TokenError(f"GitHub did not answer the token exchange ({error.__class__.__name__})") from None
    token, expires_at = data.get("token"), data.get("expires_at")
    if not token or not expires_at:
        raise TokenError("GitHub's token answer has no token or expiry")
    _write_cache(path, config, str(token), str(expires_at))
    return str(token)


def credential(op: str, stdin, stdout, config: agent_app.AppConfig | None, token) -> None:
    """git's credential helper protocol (git-credential(1)): answer `get` for github.com."""
    fields = dict(line.split("=", 1) for line in stdin.read().splitlines() if "=" in line)
    if op != "get" or config is None or fields.get("protocol") != "https" or fields.get("host") != "github.com":
        return
    stdout.write(f"username=x-access-token\npassword={token()}\n")


def _refuse_push(args: list[str]) -> None:
    refs, i = [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            if a not in GIT_PUSH_OPTIONS:
                raise TokenError(f"`git push {a}` is not a branch push for review")
        else:
            refs.append(a)
        i += 1
    if len(refs) != 2:
        raise TokenError("push exactly one branch to one remote: `git push -u origin <branch>`")
    ref = refs[1]
    dest = ref.split(":", 1)[-1]
    if (any(c in ref for c in "+*") or ref.startswith(":") or dest.startswith("refs/tags/")
            or dest.removeprefix("refs/heads/") in PROTECTED or dest.startswith("deploy-")):
        raise TokenError(f"`git push {' '.join(refs)}` is not a feature branch push; releases are not run as the App")


def plan(argv: list[str], config: agent_app.AppConfig | None, token, helper: str) -> tuple[list[str], dict]:
    """The command to run and the environment it adds; refuses anything but review work."""
    if argv[:2] == ["git", "push"]:
        _refuse_push(argv[2:])
        if config is None:
            return argv, {}
        pairs = [("credential.https://github.com.helper", ""),  # drop the user's own helper for this push
                 ("credential.https://github.com.helper", f"!python3 {helper} credential"),
                 ("url.https://github.com/.pushInsteadOf", "git@github.com:"),
                 ("url.https://github.com/.pushInsteadOf", "ssh://git@github.com/")]
        env = {"GIT_CONFIG_COUNT": str(len(pairs)), "GIT_TERMINAL_PROMPT": "0"}
        for n, (key, value) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = key, value
        return argv, env
    if argv[:2] == ["gh", "pr"] and len(argv) > 2 and argv[2] in GH_PR_SUBCOMMANDS:
        if any(a == "--attach" or a.startswith("--attach=") for a in argv):
            raise TokenError("attach evidence with your own gh (`gh pr edit <PR> --body-file <f> --attach ./<path>`): "
                             "gh uploads attachments only with a user token")
        if config is None:
            return argv, {}
        if argv[2] == "create" and not any(a in ("-r", "--reviewer") or a.startswith("--reviewer=") for a in argv):
            # The owner reviews what the App opens: GitHub puts it in their review queue.
            argv = [*argv, "--reviewer", config.owner_login]
        return argv, {"GH_TOKEN": token()}
    raise TokenError(f"`{' '.join(argv[:3])}` is not review work; only `git push <remote> <branch>` and "
                     f"`gh pr {'|'.join(sorted(GH_PR_SUBCOMMANDS))}` run here")


def main(argv: list[str]) -> int:
    try:
        config = agent_app.load_config()
    except agent_app.ConfigError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    mint = lambda: installation_token(config)  # noqa: E731
    try:
        if argv[:1] == ["credential"] and len(argv) == 2:
            credential(argv[1], sys.stdin, sys.stdout, config, mint)
            return 0
        if argv[:1] == ["status"]:
            if config is None:
                print(f"no agent App on this machine ({agent_app.config_path()} is absent): pushes and PRs run as you")
                return 0
            mint()
            with open(os.path.expanduser(CACHE), encoding="utf-8") as fh:
                expires = json.load(fh)["expiresAt"]
            print(f"agent App {config.bot_login} for {', '.join(sorted(config.repositories))}; "
                  f"reviewer {config.owner_login}; token valid until {expires}")
            return 0
        if argv[:1] == ["run"] and len(argv) > 2:
            command, extra = plan(argv[1:], config, mint, os.path.abspath(__file__))
            return subprocess.run(command, env={**os.environ, **extra}).returncode
    except TokenError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print("usage: agent-github-token.py run <git push ...|gh pr ...> | credential <get|store|erase> | status",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
