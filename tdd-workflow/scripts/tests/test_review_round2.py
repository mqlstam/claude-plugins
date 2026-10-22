"""Independent review of 1.9.0 (22e9f87), five findings (2026-10-22)."""
import importlib.util
import os
import unittest

from test_release_approval_gate import Transcript, bash_decision, gate, on_branch
from test_agent_github_token import tok

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("push_refs", os.path.join(os.path.dirname(HERE), "push_refs.py"))
push_refs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(push_refs)

WRAP = "python3 /p/claude-tdd-workflow/1.9.0/scripts/agent-github-token.py run"
H = "c" * 40


def c(cmd, branch="feature"):
    return gate.classify(cmd, "/repo", branch_of=on_branch(branch))


class ReviewWritesAreTheOwnersTest(unittest.TestCase):
    """1. An agent could write the owner's approving review with the owner's own gh,
    and the gate then accepted it. Every review write is the owner's, never covered."""

    WRITES = ("gh pr review 12 --approve", "gh pr review 12 -a", "gh pr review 12 --request-changes -b x",
              "gh pr review 12 --comment -b mooi", "gh pr review 12", "gh -R EndoxiaBV/Endoxia pr review 12 --approve",
              "gh api -X POST repos/EndoxiaBV/Endoxia/pulls/12/reviews -f event=APPROVE",
              "gh api repos/EndoxiaBV/Endoxia/pulls/12/reviews -f event=APPROVE",
              "gh api -X POST repos/EndoxiaBV/Endoxia/pulls/12/reviews/9/events -f event=APPROVE",
              "gh api -X PUT repos/EndoxiaBV/Endoxia/pulls/12/reviews/9/dismissals -f message=x",
              "gh api -X PUT repos/EndoxiaBV/Endoxia/pulls/12/reviews/9 -f body=x",
              "gh api graphql -f 'query=mutation{addPullRequestReview(input:{pullRequestId:1,event:APPROVE}){clientMutationId}}'",
              "gh api graphql -f 'query=mutation{submitPullRequestReview(input:{event:APPROVE}){clientMutationId}}'",
              "gh api graphql -f 'query=mutation{dismissPullRequestReview(input:{message:x}){clientMutationId}}'")

    def test_every_spelling_is_a_review_write(self):
        for cmd in self.WRITES:
            self.assertIn("review", c(cmd), cmd)

    def test_no_approval_covers_it(self):
        t = Transcript().typed("approve", "ship merge deploy promote release hotfix quickship")
        for cmd in self.WRITES[:11]:
            reason = bash_decision(t, cmd)
            self.assertIn("only the owner", reason or "", cmd)

    def test_reading_reviews_stays_allowed(self):
        for cmd in ("gh api repos/EndoxiaBV/Endoxia/pulls/12/reviews", "gh pr view 12 --json reviews",
                    "gh api --paginate repos/EndoxiaBV/Endoxia/pulls/12/reviews --jq .[]",
                    ):
            self.assertEqual(c(cmd), set(), cmd)
            self.assertIsNone(bash_decision(Transcript().prose("go"), cmd), cmd)
        self.assertEqual(c("gh api graphql -f 'query={repository(owner:o,name:r){pullRequest(number:1)"
                           "{reviews(first:5){totalCount}}}}'"), set())

    def test_refused_through_the_app_wrapper_too(self):
        self.assertIn("only the owner", bash_decision(Transcript().prose("go"), f"{WRAP} gh pr review 12 --approve") or "")
        with self.assertRaises(tok.TokenError):
            tok.plan(["gh", "pr", "review", "12", "--approve"], None, token=lambda: "t", helper="/h")

    def test_the_gate_says_what_it_guards_against(self):
        doc = " ".join(gate.__doc__.split())
        self.assertIn("REVIEWS ARE THE OWNER'S", doc)
        self.assertIn("take the owner's gh login out of the agents' reach", doc)
        setup = " ".join(open(os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "APP-SETUP.md")).read().split())
        self.assertIn("Follow-up: take your own gh login out of the agents' reach", setup)


class PushDestinationTest(unittest.TestCase):
    """2. `git push origin HEAD:heads/main` was no push to main. One resolver, git's
    rules, for the gate and the wrapper."""

    def test_git_resolves_a_destination_like_this(self):
        cases = {"main": ("branch", "main"), "heads/main": ("branch", "main"), "refs/heads/main": ("branch", "main"),
                 "HEAD:heads/main": ("branch", "main"), "+HEAD:main": ("branch", "main"), ":main": ("branch", "main"),
                 "x:refs/heads/master": ("branch", "master"), "HEAD": ("head", None), "@": ("head", None),
                 "x:@": ("head", None), "deploy-1": ("branch", "deploy-1"), "tags/deploy-1": ("tag", "deploy-1"),
                 "refs/tags/v1": ("tag", "v1"), "x:refs/remotes/origin/main": ("other", "refs/remotes/origin/main"),
                 "t3/feature": ("branch", "t3/feature"), "HEAD:t3/feature": ("branch", "t3/feature")}
        for ref, expected in cases.items():
            self.assertEqual(push_refs.destination(ref), expected, ref)

    def test_the_gate_reads_every_spelling_of_main(self):
        for ref in ("HEAD:heads/main", "HEAD:heads/master", "HEAD:refs/heads/main", "+HEAD:main", ":main",
                    "x:heads/main", "+t3/x:heads/main"):
            self.assertEqual(c(f"git push origin {ref}"), {"push-main"}, ref)
        self.assertEqual(c("git push origin HEAD:t3/feature"), set())

    def test_the_wrapper_refuses_every_spelling_of_main(self):
        for ref in ("HEAD:heads/main", "HEAD:heads/master", "x:refs/heads/main", "tags/deploy-1"):
            with self.assertRaises(tok.TokenError, msg=ref):
                tok.plan(["git", "push", "origin", ref], None, token=lambda: "t", helper="/h")
        argv, _env = tok.plan(["git", "push", "-u", "origin", "HEAD:t3/feature"], None, token=lambda: "t", helper="/h")
        self.assertEqual(argv[-1], "HEAD:t3/feature")


class GraphQLReleasesTest(unittest.TestCase):
    """3. `gh api graphql` mutations that merge, auto-merge or write refs were no release."""

    def test_merging_mutations_are_merges(self):
        for name in ("mergePullRequest", "enablePullRequestAutoMerge", "enqueuePullRequest"):
            self.assertIn("merge", c(f"gh api graphql -f 'query=mutation{{{name}(input:{{pullRequestId:1}}){{x}}}}'"), name)

    def test_ref_and_release_mutations_are_releases(self):
        for name, cls in (("updateRef", "deploy-tag"), ("createRef", "deploy-tag"), ("updateRefs", "deploy-tag"),
                          ("deleteRef", "deploy-tag"), ("createRelease", "deploy-tag"),
                          ("mergeBranch", "push-main"), ("createCommitOnBranch", "push-main")):
            self.assertIn(cls, c(f"gh api graphql -f 'query=mutation{{{name}(input:{{x:1}}){{y}}}}'"), name)

    def test_rest_releases_are_tags(self):
        self.assertIn("deploy-tag", c("gh api -X POST repos/EndoxiaBV/Endoxia/releases -f tag_name=v1"))

    def test_an_unreadable_query_is_refused(self):
        for cmd in ("gh api graphql -F query=@q.graphql", "gh api graphql --input q.json",
                    "gh api graphql --input -"):
            self.assertIsNotNone(bash_decision(Transcript().typed("approve", "ship"), cmd), cmd)

    def test_a_read_query_is_no_release(self):
        # A read query is no release. (A `gh api` write with braces is refused as a
        # whole by the plain-words rule, which predates this.)
        self.assertEqual(c("gh api graphql -f query={viewer{login}}"), set())


class MatchHeadReadsGhsFlagsTest(unittest.TestCase):
    """4. A value flag's value is not a head guard."""

    def test_a_value_flag_swallows_the_next_word(self):
        details = {}
        gate.classify(f"gh pr merge 12 --body --match-head-commit={H}", "/repo", branch_of=on_branch("x"),
                      details=details)
        self.assertIsNone(details["merges"][0]["head"])
        for flags in (f"-b x --match-head-commit {H}", f"--subject s -A a@b --match-head-commit={H}",
                      f"-F - --match-head-commit {H}", f"-t --x --match-head-commit {H}"):
            details = {}
            gate.classify(f"gh pr merge 12 {flags}", "/repo", branch_of=on_branch("x"), details=details)
            # pflag gives a value flag the next word whatever it looks like: `-t --x` is a subject.
            self.assertEqual(details["merges"][0]["head"], H, flags)


class NoForceAsTheAppTest(unittest.TestCase):
    """5. The docs say never force; the App's allow-list did not."""

    def test_force_flags_and_plus_refspecs_are_refused(self):
        for argv in (["git", "push", "--force-with-lease", "origin", "t3/x"],
                     ["git", "push", "--force-with-lease=t3/x", "origin", "t3/x"],
                     ["git", "push", "--force-if-includes", "origin", "t3/x"],
                     ["git", "push", "origin", "+t3/x"], ["git", "push", "origin", "+HEAD:t3/x"]):
            with self.assertRaises(tok.TokenError, msg=argv):
                tok.plan(argv, None, token=lambda: "t", helper="/h")


if __name__ == "__main__":
    unittest.main()
