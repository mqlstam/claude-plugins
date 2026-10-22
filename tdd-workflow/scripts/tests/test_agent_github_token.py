"""agent-github-token.py: the agent pushes branches and opens PRs as the GitHub App.

JWT and installation token per GitHub's docs (read 2026-10-22):
generating-a-json-web-token-jwt-for-a-github-app (RS256, iat 60 s in the past, exp at
most 10 minutes ahead, iss the client or app ID) and
generating-an-installation-access-token-for-a-github-app
(POST /app/installations/{id}/access_tokens, Bearer JWT, one hour)."""
import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "agent-github-token.py"
SPEC = importlib.util.spec_from_file_location("agent_github_token", SCRIPT)
tok = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(HERE.parent))
SPEC.loader.exec_module(tok)

NOW = 1_790_000_000
TOKEN = "ghs_" + "t" * 36


def b64(segment):
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


class FakeResponse(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.key = Path(cls.keys.name) / "app.pem"
        cls.pub = Path(cls.keys.name) / "app.pub"
        subprocess.run(["openssl", "genrsa", "-traditional", "-out", str(cls.key), "2048"], check=True,
                       capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(cls.key), "-pubout", "-out", str(cls.pub)], check=True,
                       capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = tok.agent_app.AppConfig("Iv23abc", "456", str(self.key), "endoxia-agent[bot]", "mqlstam",
                                              frozenset({"EndoxiaBV/Endoxia"}))
        self.cache = Path(self.tmp.name) / "cache" / "token.json"
        self.requests = []
        self.clock = [NOW]

    def tearDown(self):
        self.tmp.cleanup()

    def urlopen(self, request, timeout=None):
        self.requests.append((request, timeout))
        expires = tok.iso(self.clock[0] + 3600)
        return FakeResponse(json.dumps({"token": TOKEN, "expires_at": expires}).encode())

    def minted(self):
        return tok.installation_token(self.config, cache=self.cache, now=lambda: self.clock[0],
                                      urlopen=self.urlopen)


class JwtTest(Base):
    def test_the_jwt_has_githubs_claims_and_verifies_with_the_public_key(self):
        jwt = tok.app_jwt(self.config, now=NOW)
        header, payload, signature = jwt.split(".")
        self.assertEqual(b64(header), {"alg": "RS256", "typ": "JWT"})
        claims = b64(payload)
        self.assertEqual(claims["iss"], "Iv23abc")
        self.assertEqual(claims["iat"], NOW - 60)
        self.assertLessEqual(claims["exp"] - NOW, 600)
        self.assertGreater(claims["exp"], NOW)
        sig = Path(self.tmp.name) / "sig"
        sig.write_bytes(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
        verified = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(self.pub), "-signature", str(sig)],
                                  input=f"{header}.{payload}".encode(), capture_output=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_an_unreadable_key_is_refused_without_printing_it(self):
        config = self.config._replace(private_key_path=str(Path(self.tmp.name) / "missing.pem"))
        with self.assertRaisesRegex(tok.TokenError, "private key"):
            tok.app_jwt(config, now=NOW)


class InstallationTokenTest(Base):
    def test_the_exchange_posts_the_jwt_to_the_installation(self):
        self.assertEqual(self.minted(), TOKEN)
        request, timeout = self.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.github.com/app/installations/456/access_tokens")
        self.assertTrue(request.get_header("Authorization").startswith("Bearer "))
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(request.get_header("X-github-api-version"), "2022-11-28")
        self.assertEqual(json.loads(request.data), {"repositories": ["Endoxia"]})
        self.assertIsNotNone(timeout)

    def test_the_token_is_cached_private_and_reused_until_shortly_before_expiry(self):
        self.minted()
        self.assertEqual(os.stat(self.cache).st_mode & 0o777, 0o600)
        self.clock[0] += 3600 - tok.REFRESH_MARGIN_S - 1
        self.minted()
        self.assertEqual(len(self.requests), 1, "reused while it has more than the margin left")
        self.clock[0] += 2
        self.minted()
        self.assertEqual(len(self.requests), 2, "renewed inside the margin")

    def test_a_cache_of_another_installation_is_not_reused(self):
        self.minted()
        self.config = self.config._replace(installation_id="999")
        self.minted()
        self.assertEqual(len(self.requests), 2)


class CredentialHelperTest(Base):
    def credential(self, text):
        out = io.StringIO()
        tok.credential("get", io.StringIO(text), out, self.config, token=lambda: TOKEN)
        return out.getvalue()

    def test_github_https_gets_the_installation_token(self):
        out = self.credential("protocol=https\nhost=github.com\n\n")
        self.assertEqual(out, f"username=x-access-token\npassword={TOKEN}\n")

    def test_other_hosts_get_nothing(self):
        self.assertEqual(self.credential("protocol=https\nhost=gitlab.com\n\n"), "")

    def test_store_and_erase_do_nothing(self):
        out = io.StringIO()
        tok.credential("store", io.StringIO("protocol=https\nhost=github.com\n"), out, self.config,
                       token=lambda: TOKEN)
        self.assertEqual(out.getvalue(), "")


class RunTest(Base):
    """`run` executes one allowed command as the App: a branch push, a PR create or edit."""

    def planned(self, *argv, config="self"):
        return tok.plan(list(argv), self.config if config == "self" else config, token=lambda: TOKEN,
                        helper="/p/agent-github-token.py")

    def test_gh_gets_the_token_in_its_environment_only(self):
        argv, env = self.planned("gh", "pr", "edit", "12", "--body-file", "b.md")
        self.assertEqual(argv, ["gh", "pr", "edit", "12", "--body-file", "b.md"])
        self.assertEqual(env, {"GH_TOKEN": TOKEN})

    def test_a_pr_the_app_opens_asks_the_owner_for_review(self):
        argv, _env = self.planned("gh", "pr", "create", "--title", "t", "--body-file", "b.md")
        self.assertEqual(argv, ["gh", "pr", "create", "--title", "t", "--body-file", "b.md", "--reviewer", "mqlstam"])
        argv, _env = self.planned("gh", "pr", "create", "--title", "t", "--reviewer", "someone")
        self.assertEqual(argv.count("--reviewer"), 1)

    def test_git_push_uses_the_credential_helper_over_https(self):
        argv, env = self.planned("git", "push", "-u", "origin", "t3/feature")
        self.assertEqual(argv, ["git", "push", "-u", "origin", "t3/feature"])
        pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(int(env["GIT_CONFIG_COUNT"]))]
        helpers = [v for k, v in pairs if k == "credential.https://github.com.helper"]
        # An empty value first resets the user's own helpers (git-config(1), credential.helper).
        self.assertEqual(helpers, ["", "!python3 /p/agent-github-token.py credential"])
        self.assertIn(("url.https://github.com/.pushInsteadOf", "git@github.com:"), pairs)
        self.assertNotIn(TOKEN, json.dumps(env), "git asks the helper; the token is not in the environment")

    def test_releases_and_attachments_are_not_run_as_the_app(self):
        for argv in (("git", "push", "origin", "main"), ("git", "push", "origin", "HEAD:main"),
                     ("git", "push", "origin", "deploy-20261022"), ("git", "push", "--tags", "origin"),
                     ("git", "push", "origin", "refs/tags/x"), ("git", "push", "--mirror", "origin"),
                     ("git", "push", "--delete", "origin", "t3/x"), ("git", "push", "origin", ":t3/x"),
                     ("git", "push", "origin", "+t3/x"), ("git", "push", "--force", "origin", "t3/x"),
                     ("git", "push"), ("gh", "pr", "merge", "12"), ("gh", "api", "repos/o/r/pulls/1/merge"),
                     ("gh", "workflow", "run", "promote.yml"), ("gh", "pr", "create", "--attach", "./a.mp4"),
                     ("gh", "pr", "edit", "12", "--attach", "./a.mp4"), ("git", "-C", "/x", "push", "origin", "b"),
                     ("sh", "-c", "git push")):
            with self.assertRaises(tok.TokenError, msg=argv):
                self.planned(*argv)

    def test_without_an_app_the_command_runs_as_the_user(self):
        argv, env = self.planned("gh", "pr", "create", "--title", "t", config=None)
        self.assertEqual((argv, env), (["gh", "pr", "create", "--title", "t"], {}))
        with self.assertRaises(tok.TokenError):
            self.planned("gh", "pr", "merge", "12", config=None)

    def test_the_cli_never_prints_the_token(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        fake = bindir / "gh"
        fake.write_text(f"#!{sys.executable}\nimport os, sys\nprint('gh ran with a token' if os.environ.get('GH_TOKEN') "
                        "else 'gh ran as the user')\n")
        fake.chmod(0o755)
        env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
               tok.agent_app.CONFIG_ENV: str(Path(self.tmp.name) / "absent.json")}
        out = subprocess.run([sys.executable, str(SCRIPT), "run", "gh", "pr", "view", "12"], env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "gh ran as the user")
        status = subprocess.run([sys.executable, str(SCRIPT), "status"], env=env, capture_output=True, text=True)
        self.assertIn("no agent App", status.stdout)


if __name__ == "__main__":
    unittest.main()
