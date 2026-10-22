# The agent's GitHub App: setup

Why: the owner approves a change by approving its pull request on GitHub after
watching its evidence there. GitHub refuses that for your own PR ("Pull request
authors cannot approve their own pull requests"), and every PR used to be opened by
the owner's account. So the agents open their PRs as a GitHub App, the owner
reviews them, and the release gate reads that review when `/ship` merges
(`scripts/agent_app.py`). `/approve ship` keeps working beside it.

Opt-in per machine: without the config file below nothing changes, every push and
PR runs as you, and a release needs a typed approval.

Facts below were read from docs.github.com on 2026-10-22; each step names its page.

## 1. Create the App (once, in the organization)

1. GitHub, your picture, **Your organizations**, **Settings** next to the
   organization (EndoxiaBV), then **Developer settings**, **GitHub Apps**,
   **New GitHub App** (apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
2. **GitHub App name**: for example `endoxia-agent` (at most 34 characters; GitHub
   shows it as a lowercase hyphenated slug). Its bot login is then
   `endoxia-agent[bot]`: that is how the REST API names the author of its PRs.
3. **Homepage URL**: the repository's URL will do.
4. **Webhook**: deselect **Active**. Nothing listens; T3 Code watches the PR itself.
5. **Repository permissions**, nothing else (rest/authentication/permissions-required-for-github-apps).
   Only what the plugin runs as the App needs a permission: pushing a feature branch
   and writing PR text (`scripts/agent-github-token.py run`).
   - **Contents: Read and write**: `git push` of the feature branch ("request the
     Contents repository permission" for git over HTTPS).
   - **Pull requests: Read and write**: `gh pr create`, `gh pr edit`, `gh pr view`,
     `gh pr comment`, `gh pr ready`, and requesting the owner as reviewer
     (`POST /pulls`, `PATCH /pulls/{n}` need write).
   - **Metadata: Read-only**: the base read of the repository every call makes
     (`GET /repos/{o}/{r}`). GitHub lists it with every App.
   - **Workflows: Read and write**, ONLY if agents change files under
     `.github/workflows/`: GitHub refuses a push that touches them without it. It
     has no read-only level. Leave it off otherwise, and such a push then fails out
     loud.
   Not needed, because these run with your own `gh`, not the App's token: the merge,
   the `deploy-*` tag push and branch deletion (Contents write would allow them;
   the wrapper refuses them, and the release gate refuses a release through it),
   `gh run view` / `gh run list` (Actions), check runs and their annotations
   (Checks), commit statuses such as `endoxia/main-health`, environment variables
   such as staging's `PUBLIC_URL` (Environments), `gh workflow run` (Actions write),
   and evidence uploads: gh uploads attachments only with a user token
   (`internal/attachments/client.go` accepts `gho_`, `ghp_`, `github_pat_`, `ghu_`,
   never an installation's `ghs_`), so `gh pr edit --attach` runs as you.
6. **Where can this GitHub App be installed?**: **Only on this account**. Then
   **Create GitHub App**.
7. On the App's page note the **App ID** and the **Client ID** (they differ; the
   JWT's issuer may be either, the Client ID is recommended:
   apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app).

## 2. The private key

On the App's page: **Edit**, then under **Key pairs** ("Private keys" on older pages) **New key**.
GitHub downloads a `.pem` and keeps only the public half
(apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps).
Move it out of Downloads and lock it:

```bash
mkdir -p ~/.config/claude-tdd-workflow
mv ~/Downloads/endoxia-agent.*.private-key.pem ~/.config/claude-tdd-workflow/agent-app.private-key.pem
chmod 600 ~/.config/claude-tdd-workflow/agent-app.private-key.pem
```

## 3. Install it on the one repository

On the App's page: **Install App**, **Install** next to the organization, **Only
select repositories**, pick `EndoxiaBV/Endoxia`, **Install**
(apps/using-github-apps/installing-your-own-github-app). The page you land on is
`https://github.com/organizations/EndoxiaBV/settings/installations/<INSTALLATION_ID>`:
that number is the installation id.

## 4. The config file

`~/.config/claude-tdd-workflow/github-app.json` (or the path in
`CLAUDE_TDD_GITHUB_APP_CONFIG`), outside every repository:

```json
{
  "clientId": "<Client ID>",
  "installationId": 12345678,
  "privateKeyPath": "~/.config/claude-tdd-workflow/agent-app.private-key.pem",
  "botLogin": "endoxia-agent[bot]",
  "ownerLogin": "mqlstam",
  "repositories": ["EndoxiaBV/Endoxia"]
}
```

```bash
chmod 600 ~/.config/claude-tdd-workflow/github-app.json
```

The tools refuse a config others can read. `ownerLogin` is the one account whose
review approves a merge; `botLogin` is the author a PR must have for that; neither
is written anywhere else. `appId` may stand in for `clientId`.

## 5. Check it

In a session (the plugin's root is `${CLAUDE_PLUGIN_ROOT}` there):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent-github-token.py status
```

It mints a token and prints the bot, the repositories, the reviewer and when the
token expires; it never prints the key or the token. Then reload the plugin
(`/reload-plugins`) or start a new session: hooks are read at session start.

## How it works

- **The token.** A JWT signed with the key (RS256, `iat` 60 s in the past against
  clock drift, `exp` 9 minutes ahead where GitHub allows at most 10, `iss` the
  Client ID), exchanged at `POST /app/installations/{id}/access_tokens` for an
  installation access token that lives one hour and reaches only the configured
  repositories. It is cached mode 600 in `~/.cache/claude-tdd-workflow/` and
  renewed 10 minutes before it expires
  (apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app).
- **git** gets it through a credential helper set for that one push only
  (`GIT_CONFIG_*` in the push's environment): user `x-access-token`, the token as
  password (apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).
  An SSH `origin` is pushed over HTTPS for that push. Your own git config is not
  changed.
- **gh** gets it as `GH_TOKEN` in its environment for that one call (GH_TOKEN takes
  precedence over stored credentials, cli.github.com/manual/gh_help_environment).
- **Workflows still run.** Events made with the `GITHUB_TOKEN` of a workflow start
  no new workflow run, but an installation access token does trigger them ("you can
  use a GitHub App installation access token ... instead of GITHUB_TOKEN";
  actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow). The
  App's branch pushes and PRs start their checks as yours did, without the approval
  prompt for `pull_request` runs. `deploy-*` tags are pushed with your own
  identity, as before, and start Deploy as before; pushed with an installation
  token they would trigger it too (the docs do not name tag pushes separately; they
  are push events).
- **The approval.** When `/ship` merges, the release gate reads
  `GET /repos/{o}/{r}/pulls/{n}` and `/pulls/{n}/reviews`: the PR's author must be
  `botLogin`, and `ownerLogin`'s newest submitted review must be `APPROVED` with
  `commit_id` equal to the head the merge names (`--match-head-commit`). As on
  GitHub, a comment after the approval keeps it; a dismissal, a later "changes
  requested" or a newer commit means no approval. It covers that one merge and the one staging tag on its
  merge commit, and is spent by the merge taking effect. Deploy, promote, release
  and hotfix are never approved by a review.

## Follow-up: take your own gh login out of the agents' reach

The release gate refuses an agent writing a review in any form it can read
(`gh pr review`, the REST and GraphQL review writes), because your approving review
is the approval. That is a text guard against an agent's mistake, like the rest of
the gate, not a wall: the agents run with your own `gh` login, which can review.
The hard version is an agent that holds no identity but the App's (which GitHub
never lets approve its own PR): your `gh` login kept out of the agents' sessions,
with merges, tags and evidence uploads moved to an identity that cannot review for
you. Not done in 1.9.0.
