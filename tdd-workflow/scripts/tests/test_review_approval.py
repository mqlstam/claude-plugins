"""Owner, 2026-10-22: the owner approves a change by approving its pull request on
GitHub, after watching its evidence there. The release gate reads that review at the
moment of landing; `/approve ship` keeps working beside it."""
import json
import os
import tempfile
import unittest

from test_release_approval_gate import (FakeWorld, M, OTHER, Transcript, bash_decision, gate,
                                        skill_decision, status_text)

import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("agent_app", os.path.join(os.path.dirname(HERE), "agent_app.py"))
agent_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_app)

HEAD = "c" * 40
OLD = "d" * 40
BOT = "endoxia-agent[bot]"
OWNER = "mqlstam"
CONFIG = agent_app.AppConfig("123", "456", "/k.pem", BOT, OWNER, frozenset({"endoxiabv/endoxia"}))
MERGE = f"gh pr merge 12 --squash --match-head-commit {HEAD}"


def review(state, commit=HEAD, login=OWNER):
    return {"user": {"login": login}, "state": state, "commit_id": commit}


class Answer:
    def __init__(self, code, stdout):
        self.returncode, self.stdout = code, stdout


class ReviewWorld(FakeWorld):
    """FakeWorld, and GitHub's REST answers for PR 12: who opened it, its head, its reviews."""

    def __init__(self, reviews=(), author=BOT, head=HEAD, config=CONFIG, fail=False, branch_prs=("12",), **kw):
        super().__init__(**kw)
        self.app_config = config
        self.calls = []
        pr = {"user": {"login": author}, "head": {"sha": head}, "state": "open"}

        def run(argv, **_kw):
            self.calls.append(argv)
            if fail:
                return Answer(1, "")
            path = next(a for a in argv if a.startswith("repos/"))
            if path == "repos/endoxiabv/endoxia/pulls/12":
                return Answer(0, json.dumps(pr) + "\n")
            if path == "repos/endoxiabv/endoxia/pulls/12/reviews":
                return Answer(0, "".join(json.dumps(r) + "\n" for r in reviews))
            if path.startswith("repos/endoxiabv/endoxia/pulls?head=endoxiabv:"):
                return Answer(0, "".join(n + "\n" for n in branch_prs))
            if path.startswith("repos/endoxiabv/endoxia/pulls/"):
                return Answer(1, "")  # gh: HTTP 404, no such PR
            raise AssertionError(f"unexpected gh call {argv}")

        self._run = run

    def current_branch(self, directory):
        return "feature"


def approved_world(**kw):
    return ReviewWorld(reviews=[review("APPROVED")], **kw)


class JudgeReviewTest(unittest.TestCase):
    def judge(self, reviews, author=BOT, head=HEAD):
        return agent_app.judge_review({"user": {"login": author}, "head": {"sha": head}}, reviews, CONFIG)

    def test_the_owners_approval_of_the_head_approves(self):
        self.assertTrue(self.judge([review("APPROVED")]).approved)

    def test_an_approval_of_an_older_head_does_not(self):
        verdict = self.judge([review("APPROVED", commit=OLD)])
        self.assertFalse(verdict.approved)
        self.assertIn("a commit came after the approval", verdict.reason)

    def test_an_approval_by_someone_else_does_not(self):
        verdict = self.judge([review("APPROVED", login="someone-else")])
        self.assertFalse(verdict.approved)
        self.assertIn("has not approved", verdict.reason)

    def test_a_pr_the_owner_opened_is_not_approved_by_a_review(self):
        verdict = self.judge([review("APPROVED")], author=OWNER)
        self.assertFalse(verdict.approved)
        self.assertIn("/approve ship", verdict.reason)

    def test_a_dismissed_review_does_not(self):
        # GitHub rewrites a dismissed review's state to DISMISSED.
        self.assertFalse(self.judge([review("DISMISSED")]).approved)

    def test_changes_requested_after_an_approval_does_not(self):
        # Mutation: taking any APPROVED review instead of the newest one passes this.
        verdict = self.judge([review("APPROVED"), review("CHANGES_REQUESTED")])
        self.assertFalse(verdict.approved)
        self.assertIn("CHANGES_REQUESTED", verdict.reason)

    def test_a_comment_after_the_approval_keeps_it(self):
        # Owner, 2026-10-22: GitHub's rule. A "mooi" after approving must not block;
        # only a later CHANGES_REQUESTED, a dismissal or a new head voids it.
        # Mutation: counting COMMENTED as the newest review refuses this.
        self.assertTrue(self.judge([review("APPROVED"), review("COMMENTED")]).approved)
        self.assertFalse(self.judge([review("APPROVED"), review("COMMENTED"), review("CHANGES_REQUESTED")]).approved)

    def test_a_comment_alone_approves_nothing(self):
        verdict = self.judge([review("COMMENTED")])
        self.assertFalse(verdict.approved)
        self.assertIn("has not approved or requested changes", verdict.reason)

    def test_an_approval_after_changes_requested_approves_and_pending_is_ignored(self):
        self.assertTrue(self.judge([review("CHANGES_REQUESTED", commit=OLD), review("APPROVED"),
                                    review("PENDING")]).approved)

    def test_logins_compare_without_case(self):
        self.assertTrue(self.judge([review("APPROVED", login="MQLStam")], author="Endoxia-Agent[bot]").approved)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "github-app.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data, mode=0o600):
        with open(self.path, "w") as fh:
            json.dump(data, fh)
        os.chmod(self.path, mode)

    def test_absent_config_is_no_app(self):
        self.assertIsNone(agent_app.load_config(self.path))

    def test_a_complete_config_loads(self):
        self.write({"appId": 1, "installationId": 2, "privateKeyPath": "~/k.pem", "botLogin": BOT,
                    "ownerLogin": OWNER, "repositories": ["EndoxiaBV/Endoxia"]})
        config = agent_app.load_config(self.path)
        self.assertEqual(config.issuer, "1")
        self.assertTrue(config.covers("endoxiabv/endoxia"))
        self.assertFalse(config.covers("mqlstam/eabos"))
        self.assertFalse(config.private_key_path.startswith("~"))

    def test_a_config_others_can_read_is_refused(self):
        self.write({"appId": 1}, mode=0o644)
        with self.assertRaisesRegex(agent_app.ConfigError, "chmod 600"):
            agent_app.load_config(self.path)

    def test_a_config_without_the_logins_is_refused(self):
        self.write({"appId": 1, "installationId": 2, "privateKeyPath": "k", "repositories": ["a/b"]})
        with self.assertRaisesRegex(agent_app.ConfigError, "botLogin, ownerLogin"):
            agent_app.load_config(self.path)


class ReviewApprovesTheMergeTest(unittest.TestCase):
    def test_an_approved_head_merges_without_a_typed_approval(self):
        world = approved_world()
        self.assertIsNone(bash_decision(Transcript().prose("go"), MERGE, world=world))
        self.assertIn(["gh", "api", "--paginate", "repos/endoxiabv/endoxia/pulls/12/reviews", "--jq",
                       ".[] | {user: {login: .user.login}, state: .state, commit_id: .commit_id}"], world.calls)

    def test_each_rejected_review_refuses_the_merge_and_says_why(self):
        for reviews, author, why in (
                ([review("APPROVED", commit=OLD)], BOT, "a commit came after the approval"),
                ([review("APPROVED", login="someone-else")], BOT, "has not approved"),
                ([review("APPROVED")], OWNER, "not by the agent App"),
                ([review("DISMISSED")], BOT, "DISMISSED"),
                ([review("APPROVED"), review("CHANGES_REQUESTED")], BOT, "CHANGES_REQUESTED"),
                ([], BOT, "has not approved")):
            reason = bash_decision(Transcript().prose("go"), MERGE, world=ReviewWorld(reviews=reviews, author=author))
            self.assertIn("merge", reason, why)
            self.assertIn(why, reason, why)

    def test_the_merge_must_match_the_approved_head(self):
        # Without --match-head-commit GitHub merges whatever the head is by then.
        world = approved_world()
        for cmd in ("gh pr merge 12 --squash", f"gh pr merge 12 --squash --match-head-commit {OLD}",
                    f"gh pr merge 13 --squash --match-head-commit {HEAD}"):
            self.assertIsNotNone(bash_decision(Transcript().prose("go"), cmd, world=world), cmd)

    def test_a_repeated_head_flag_is_never_approved(self):
        # Review finding (2026-10-22): gh keeps the LAST value of a repeated flag, the
        # gate read the first. Mutation: reading the first value allows this merge.
        world = approved_world()
        for cmd in (f"{MERGE} --match-head-commit {OLD}", f"gh pr merge 12 --match-head-commit={HEAD} "
                                                         f"--match-head-commit {OLD}"):
            self.assertIsNotNone(bash_decision(Transcript().prose("go"), cmd, world=world), cmd)

    def test_no_app_config_means_typed_approvals_only(self):
        world = approved_world(config=None)
        self.assertIsNotNone(bash_decision(Transcript().prose("go"), MERGE, world=world))
        self.assertIsNone(bash_decision(Transcript().typed("approve", "ship"), MERGE, world=world))

    def test_a_repository_outside_the_config_is_not_approved_by_a_review(self):
        config = CONFIG._replace(repositories=frozenset({"mqlstam/eabos"}))
        self.assertIsNotNone(bash_decision(Transcript().prose("go"), MERGE, world=approved_world(config=config)))

    def test_an_unreadable_review_refuses(self):
        reason = bash_decision(Transcript().prose("go"), MERGE, world=approved_world(fail=True))
        self.assertIn("could not be read", reason)

    def test_the_review_approves_one_merge_and_one_staging_tag_on_its_merge_commit(self):
        t = Transcript().prose("go")
        t.bash(MERGE)
        merged = dict(prs={"12": ("MERGED", M)})
        # The merged PR keeps its approved head, so the replay finds the review again.
        self.assertIsNone(bash_decision(t, "git push origin deploy-1",
                                        world=approved_world(local_tags={"deploy-1": M}, **merged)))
        self.assertIn("deploy-tag", bash_decision(t, "git push origin deploy-1",
                                                  world=approved_world(local_tags={"deploy-1": OTHER}, **merged)))
        t.bash("git push origin deploy-1")
        world = approved_world(local_tags={"deploy-2": M}, remote_tags={"deploy-1": M}, **merged)
        self.assertIn("deploy-tag", bash_decision(t, "git push origin deploy-2", world=world))
        self.assertIsNotNone(bash_decision(t, "gh workflow run promote.yml -f sha=" + M, world=world))

    def test_a_failed_merge_spends_nothing(self):
        t = Transcript().prose("go")
        t.bash(MERGE, error=True, output="Exit code 1\nhead changed")
        self.assertIsNone(bash_decision(t, MERGE, world=approved_world()))
        self.assertIn("deploy-tag", bash_decision(t, "git push origin deploy-1",
                                                  world=approved_world(local_tags={"deploy-1": M})))

    def test_a_typed_approval_is_spent_first_and_the_review_is_not_counted_twice(self):
        t = Transcript().typed("approve", "ship")
        t.bash(MERGE)
        world = approved_world(prs={"12": ("MERGED", M)})
        # The typed approval paid for the merge; its follow-on covers the one tag.
        self.assertIsNone(bash_decision(t, "git push origin deploy-1",
                                        world=approved_world(prs={"12": ("MERGED", M)}, local_tags={"deploy-1": M})))
        self.assertNotIn("ship (merge)", status_text(t, world=world))
        # Merging it again (a retry after MERGED) adds no second follow-on.
        t.bash(MERGE)
        t.bash("git push origin deploy-1")
        world = approved_world(prs={"12": ("MERGED", M)}, remote_tags={"deploy-1": M}, local_tags={"deploy-2": M})
        self.assertIn("deploy-tag", bash_decision(t, "git push origin deploy-2", world=world))


class AtIsHeadTest(unittest.TestCase):
    def test_a_push_of_at_from_main_is_a_push_to_main(self):
        # Review finding: git reads `@` as HEAD. Mutation: without the alias it is no release.
        for cmd in ("git push origin @", "git push origin @:@"):
            self.assertEqual(gate.classify(cmd, "/repo", branch_of=lambda _d: "main"), {"push-main"}, cmd)
        self.assertEqual(gate.classify("git push origin @", "/repo", branch_of=lambda _d: "feature"), set())


class ReviewApprovesTheShipSkillTest(unittest.TestCase):
    def test_ship_may_run_when_the_branchs_pr_is_approved_on_its_head(self):
        self.assertIsNone(skill_decision(Transcript().prose("go"), "claude-tdd-workflow:ship",
                                         world=approved_world(), cwd="/repo"))

    def test_ship_is_refused_without_an_approval_and_names_both_ways(self):
        reason = skill_decision(Transcript().prose("go"), "claude-tdd-workflow:ship",
                                world=ReviewWorld(reviews=[review("CHANGES_REQUESTED")]), cwd="/repo")
        self.assertIn("/approve ship", reason)
        self.assertIn("CHANGES_REQUESTED", reason)

    def test_deploy_and_production_are_never_approved_by_a_review(self):
        for name in ("deploy", "promote", "release", "hotfix"):
            self.assertIsNotNone(skill_decision(Transcript().prose("go"), name, world=approved_world(),
                                                cwd="/repo"), name)

    def test_a_branch_without_one_open_pr_is_not_approved(self):
        for prs in ((), ("12", "13")):
            self.assertIsNotNone(skill_decision(Transcript().prose("go"), "claude-tdd-workflow:ship",
                                                world=approved_world(branch_prs=prs), cwd="/repo"), prs)


if __name__ == "__main__":
    unittest.main()
